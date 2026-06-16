from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import uuid
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import logger, settings
from app.core.constants import (
    SeverityLevel, TicketStatus, LogActionType,
    TicketStatusCN
)
from app.core.database import get_db_context
from app.models.investigation import InvestigationTicket
from app.models.organization import Employee, Department
from app.models.compliance import SystemLog, EventTimeline


class TicketEscalationService:
    ESCALATION_INTERVALS = {
        SeverityLevel.CRITICAL: timedelta(hours=12),
        SeverityLevel.IMPORTANT: timedelta(hours=48),
        SeverityLevel.GENERAL: timedelta(days=5),
    }

    REMINDER_INTERVAL = timedelta(hours=24)
    MAX_ESCALATIONS = 3

    def __init__(self):
        self.logger = logger.bind(module="TicketEscalationService")

    async def check_and_process_overdue(self) -> Dict[str, int]:
        stats = {
            "overdue_detected": 0,
            "escalated": 0,
            "reminders_sent": 0,
        }

        async with get_db_context() as db:
            active_tickets = await db.execute(
                select(InvestigationTicket).where(
                    InvestigationTicket.status.in_([
                        TicketStatus.ASSIGNED.value,
                        TicketStatus.UNDER_INVESTIGATION.value,
                        TicketStatus.EVIDENCE_COLLECTED.value,
                        TicketStatus.CONCLUSION_SUBMITTED.value,
                    ])
                )
            )
            tickets = active_tickets.scalars().all()

            now = datetime.utcnow()

            for ticket in tickets:
                try:
                    if ticket.deadline and now > ticket.deadline:
                        if not ticket.is_overdue:
                            ticket.is_overdue = True
                            stats["overdue_detected"] += 1
                            await self._mark_overdue(db, ticket)

                    updated = await self._maybe_escalate(db, ticket, now)
                    if updated:
                        stats["escalated"] += 1

                    reminded = await self._maybe_send_reminder(db, ticket, now)
                    if reminded:
                        stats["reminders_sent"] += 1

                except Exception as e:
                    self.logger.error(
                        "Failed to process escalation for ticket",
                        ticket_number=ticket.ticket_number,
                        error=str(e)
                    )

        self.logger.info("Escalation processing complete", **stats)
        return stats

    async def _maybe_escalate(
        self, db: AsyncSession, ticket: InvestigationTicket, now: datetime
    ) -> bool:
        severity = SeverityLevel(ticket.severity)
        escalation_interval = self.ESCALATION_INTERVALS.get(severity, timedelta(days=7))

        if ticket.assigned_at:
            time_elapsed = now - ticket.assigned_at
            next_escalation_due = escalation_interval * (ticket.escalation_count + 1)

            if time_elapsed >= next_escalation_due and ticket.escalation_count < self.MAX_ESCALATIONS:
                return await self._escalate_ticket(db, ticket, now)

        if ticket.is_overdue and (now - ticket.deadline) >= escalation_interval:
            if ticket.escalation_count < self.MAX_ESCALATIONS:
                return await self._escalate_ticket(db, ticket, now)

        return False

    async def _escalate_ticket(
        self, db: AsyncSession, ticket: InvestigationTicket, now: datetime
    ) -> bool:
        supervisor_id = None
        supervisor_name = None

        if ticket.assigned_officer_id:
            officer_result = await db.execute(
                select(Employee).where(Employee.id == ticket.assigned_officer_id)
            )
            officer = officer_result.scalar_one_or_none()
            if officer and officer.supervisor_id:
                supervisor_id = officer.supervisor_id
                sup_result = await db.execute(
                    select(Employee).where(Employee.id == supervisor_id)
                )
                supervisor = sup_result.scalar_one_or_none()
                if supervisor:
                    supervisor_name = supervisor.name

        if not supervisor_id and ticket.department_id:
            dept_result = await db.execute(
                select(Department).where(Department.id == ticket.department_id)
            )
            dept = dept_result.scalar_one_or_none()
            if dept and dept.manager_id:
                supervisor_id = dept.manager_id
                mgr_result = await db.execute(
                    select(Employee).where(Employee.id == dept.manager_id)
                )
                mgr = mgr_result.scalar_one_or_none()
                if mgr:
                    supervisor_name = mgr.name

        ticket.escalated_at = now
        ticket.escalated_to_id = supervisor_id
        ticket.escalated_to_name = supervisor_name
        ticket.escalation_count = (ticket.escalation_count or 0) + 1
        ticket.status = TicketStatus.ESCALATED.value

        timeline = EventTimeline(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            employee_id=ticket.assigned_officer_id,
            employee_name=ticket.assigned_officer_name,
            timestamp=now,
            timeline_type="escalation",
            title=f"工单自动升级至第{ticket.escalation_count}级",
            description=(
                f"工单{ticket.ticket_number}因处理超时自动升级。"
                f"升级至: {supervisor_name or '上级主管'}，"
                f"已逾期: {self._format_duration(now - ticket.deadline) if ticket.deadline else 'N/A'}"
            ),
            is_key_event=True,
        )
        db.add(timeline)

        log = SystemLog(
            id=uuid.uuid4(),
            log_level="WARNING",
            action_type=LogActionType.TICKET_ESCALATED.value,
            target_type="investigation_ticket",
            target_id=ticket.id,
            target_name=ticket.ticket_number,
            user_id=ticket.escalated_to_id,
            user_name=supervisor_name,
            action_details={
                "escalation_level": ticket.escalation_count,
                "original_assignee": str(ticket.assigned_officer_id) if ticket.assigned_officer_id else None,
                "is_overdue": ticket.is_overdue,
                "deadline": ticket.deadline.isoformat() if ticket.deadline else None,
            },
            status="success",
        )
        db.add(log)

        return True

    async def _maybe_send_reminder(
        self, db: AsyncSession, ticket: InvestigationTicket, now: datetime
    ) -> bool:
        if not ticket.assigned_officer_id:
            return False

        last_reminder = ticket.last_reminder_at
        if last_reminder is None:
            if ticket.assigned_at:
                elapsed = now - ticket.assigned_at
            else:
                elapsed = timedelta(0)
        else:
            elapsed = now - last_reminder

        should_remind = False
        if last_reminder is None:
            if ticket.is_overdue:
                should_remind = True
            elif elapsed >= self.REMINDER_INTERVAL:
                should_remind = True
        else:
            if elapsed >= self.REMINDER_INTERVAL:
                should_remind = True

        if should_remind:
            ticket.last_reminder_at = now
            ticket.reminder_count = (ticket.reminder_count or 0) + 1

            timeline = EventTimeline(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                employee_id=ticket.assigned_officer_id,
                employee_name=ticket.assigned_officer_name,
                timestamp=now,
                timeline_type="reminder",
                title=f"处理时限提醒（第{ticket.reminder_count}次）",
                description=(
                    f"请尽快处理工单 {ticket.ticket_number}。"
                    + (f"已逾期 {self._format_duration(now - ticket.deadline)}！" if ticket.is_overdue and ticket.deadline
                       else f"剩余 {self._format_duration(ticket.deadline - now)}" if ticket.deadline else "")
                ),
                is_key_event=False,
            )
            db.add(timeline)
            return True

        return False

    async def _mark_overdue(self, db: AsyncSession, ticket: InvestigationTicket):
        timeline = EventTimeline(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            employee_id=ticket.assigned_officer_id,
            employee_name=ticket.assigned_officer_name,
            timestamp=datetime.utcnow(),
            timeline_type="overdue",
            title="工单已逾期",
            description=(
                f"工单 {ticket.ticket_number} 已超过处理时限。"
                f"原定截止: {ticket.deadline.strftime('%Y-%m-%d %H:%M') if ticket.deadline else 'N/A'}"
            ),
            is_key_event=True,
        )
        db.add(timeline)

        log = SystemLog(
            id=uuid.uuid4(),
            log_level="WARNING",
            action_type="ticket_overdue",
            target_type="investigation_ticket",
            target_id=ticket.id,
            target_name=ticket.ticket_number,
            action_details={
                "deadline": ticket.deadline.isoformat() if ticket.deadline else None,
                "assigned_officer": str(ticket.assigned_officer_id) if ticket.assigned_officer_id else None,
            },
            status="success",
        )
        db.add(log)

    @staticmethod
    def _format_duration(delta: timedelta) -> str:
        total_seconds = int(delta.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes}分钟")

        return "".join(parts) if parts else "0分钟"
