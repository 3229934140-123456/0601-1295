from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import uuid
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import logger
from app.core.constants import (
    SeverityLevel, TicketStatus, ViolationResult,
    DisciplinaryAction, InvestigationConclusion,
    LogActionType
)
from app.core.database import get_db_context
from app.models.investigation import InvestigationTicket, EvidenceItem
from app.models.organization import Employee
from app.models.compliance import (
    ComplianceProfile, ComplianceProfileHistory,
    EventTimeline, SystemLog, ApprovalRecord, DisciplinaryActionRecord
)


class InvestigationWorkflowService:
    SCORE_PENALTY_MAP = {
        DisciplinaryAction.WARNING: -10,
        DisciplinaryAction.SERIOUS_WARNING: -20,
        DisciplinaryAction.DEMOTION: -30,
        DisciplinaryAction.SALARY_REDUCTION: -25,
        DisciplinaryAction.PERMISSION_FREEZE: -35,
        DisciplinaryAction.TERMINATION: -100,
        DisciplinaryAction.TRAINING: -5,
        DisciplinaryAction.NO_ACTION: 0,
    }

    def __init__(self):
        self.logger = logger.bind(module="InvestigationWorkflowService")

    async def start_investigation(
        self, ticket_id: uuid.UUID, officer_id: uuid.UUID, notes: str = None
    ) -> InvestigationTicket:
        async with get_db_context() as db:
            ticket = await self._get_ticket(db, ticket_id)

            if ticket.assigned_officer_id != officer_id:
                raise PermissionError("This ticket is assigned to a different officer")

            if ticket.status not in [TicketStatus.ASSIGNED.value, TicketStatus.ESCALATED.value]:
                raise ValueError(f"Cannot start investigation from status: {ticket.status}")

            ticket.status = TicketStatus.UNDER_INVESTIGATION.value
            ticket.investigation_notes = notes or ""

            timeline = EventTimeline(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                employee_id=officer_id,
                timestamp=datetime.utcnow(),
                timeline_type="investigation_start",
                title="调查开始",
                description=f"调查专员启动调查" + (f"\n备注: {notes}" if notes else ""),
                is_key_event=True,
            )
            db.add(timeline)

            await self._log_action(
                db, "investigation_start", ticket, officer_id,
                {"notes": notes}
            )

            self.logger.info(
                "Investigation started",
                ticket_number=ticket.ticket_number,
                officer_id=str(officer_id)
            )
            return ticket

    async def submit_conclusion(
        self,
        ticket_id: uuid.UUID,
        officer_id: uuid.UUID,
        conclusion: InvestigationConclusion,
        conclusion_text: str,
        violation_result: ViolationResult,
        disciplinary_action: Optional[DisciplinaryAction] = None,
        action_details: Optional[Dict[str, Any]] = None,
        estimated_hours: float = 0,
    ) -> InvestigationTicket:
        async with get_db_context() as db:
            ticket = await self._get_ticket(db, ticket_id)

            if ticket.assigned_officer_id != officer_id:
                raise PermissionError("This ticket is assigned to a different officer")

            if ticket.status != TicketStatus.UNDER_INVESTIGATION.value:
                raise ValueError(f"Cannot submit conclusion from status: {ticket.status}")

            ticket.status = TicketStatus.CONCLUSION_SUBMITTED.value
            ticket.conclusion_text = conclusion_text
            ticket.violation_result = violation_result.value
            ticket.estimated_hours = estimated_hours
            ticket.actual_hours = estimated_hours

            if violation_result == ViolationResult.CONFIRMED and disciplinary_action:
                ticket.disciplinary_action = disciplinary_action.value

            conclusion_summary = self._generate_conclusion_summary(
                ticket, conclusion, violation_result, disciplinary_action
            )
            ticket.conclusion_summary = conclusion_summary

            timeline = EventTimeline(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                employee_id=officer_id,
                timestamp=datetime.utcnow(),
                timeline_type="conclusion_submit",
                title="调查结论提交",
                description=(
                    f"结论: {conclusion.value}\n"
                    f"违规认定: {violation_result.value}\n"
                    + (f"建议处分: {disciplinary_action.value if disciplinary_action else '无'}\n" if violation_result == ViolationResult.CONFIRMED else "")
                    + f"\n详细说明: {conclusion_text}"
                ),
                is_key_event=True,
            )
            db.add(timeline)

            await self._initiate_approval_flow(db, ticket)

            await self._log_action(
                db, LogActionType.CONCLUSION_SUBMITTED.value, ticket, officer_id,
                {
                    "conclusion": conclusion.value,
                    "violation_result": violation_result.value,
                    "disciplinary_action": disciplinary_action.value if disciplinary_action else None,
                    "estimated_hours": estimated_hours,
                }
            )

            self.logger.info(
                "Conclusion submitted",
                ticket_number=ticket.ticket_number,
                conclusion=conclusion.value,
                violation_result=violation_result.value
            )
            return ticket

    async def complete_investigation(
        self,
        ticket_id: uuid.UUID,
        approver_id: uuid.UUID,
        approval_decision: str,
        approval_comments: Optional[str] = None,
    ) -> InvestigationTicket:
        async with get_db_context() as db:
            ticket = await self._get_ticket(db, ticket_id)

            if approval_decision not in ["approved", "rejected"]:
                raise ValueError("Invalid approval decision")

            approval_result = await db.execute(
                select(ApprovalRecord).where(
                    and_(
                        ApprovalRecord.ticket_id == ticket.id,
                        ApprovalRecord.approver_id == approver_id,
                        ApprovalRecord.status == "pending"
                    )
                )
            )
            approval = approval_result.scalar_one_or_none()
            if not approval:
                raise ValueError("No pending approval found for this approver")

            approval.status = "decided"
            approval.decision = approval_decision
            approval.comments = approval_comments
            approval.decided_at = datetime.utcnow()

            all_pending = await db.execute(
                select(ApprovalRecord).where(
                    and_(
                        ApprovalRecord.ticket_id == ticket.id,
                        ApprovalRecord.status == "pending"
                    )
                )
            )
            has_more_pending = len(all_pending.scalars().all()) > 0

            if approval_decision == "rejected":
                ticket.status = TicketStatus.REJECTED.value
                timeline_desc = "调查结论被驳回"
                if approval_comments:
                    timeline_desc += f"\n驳回理由: {approval_comments}"

                timeline = EventTimeline(
                    id=uuid.uuid4(),
                    ticket_id=ticket.id,
                    employee_id=approver_id,
                    timestamp=datetime.utcnow(),
                    timeline_type="approval_rejected",
                    title="审批驳回",
                    description=timeline_desc,
                    is_key_event=True,
                )
                db.add(timeline)
                return ticket

            if not has_more_pending:
                if ticket.violation_result == ViolationResult.CONFIRMED.value:
                    await self._execute_disciplinary_action(db, ticket, approver_id)

                await self._update_compliance_profile(db, ticket)

                ticket.status = TicketStatus.CLOSED.value
                ticket.closed_at = datetime.utcnow()
                ticket.closed_by_id = approver_id
                ticket.closed_reason = "Investigation completed and approved"

                timeline = EventTimeline(
                    id=uuid.uuid4(),
                    ticket_id=ticket.id,
                    employee_id=approver_id,
                    timestamp=datetime.utcnow(),
                    timeline_type="ticket_closed",
                    title="调查完成 - 工单关闭",
                    description=(
                        f"所有审批通过，调查流程完成\n"
                        f"最终结论: {ticket.violation_result}"
                        + (f"\n处分执行: {ticket.disciplinary_action}" if ticket.disciplinary_action else "")
                    ),
                    is_key_event=True,
                )
                db.add(timeline)

                await self._log_action(
                    db, LogActionType.TICKET_CLOSED.value, ticket, approver_id,
                    {
                        "final_status": ticket.status,
                        "violation_result": ticket.violation_result,
                        "disciplinary_action": ticket.disciplinary_action,
                    }
                )

            return ticket

    async def _initiate_approval_flow(
        self, db: AsyncSession, ticket: InvestigationTicket
    ):
        severity = SeverityLevel(ticket.severity)
        violation_result = ViolationResult(ticket.violation_result)

        approver_levels = []

        if severity == SeverityLevel.CRITICAL or violation_result == ViolationResult.CONFIRMED:
            approver_levels = [
                ("合规主管", 1, 3),
                ("部门总监", 2, 3),
                ("合规委员会", 3, 3),
            ]
        elif severity == SeverityLevel.IMPORTANT:
            approver_levels = [
                ("合规主管", 1, 2),
                ("部门总监", 2, 2),
            ]
        else:
            approver_levels = [
                ("合规主管", 1, 1),
            ]

        dept_manager_id = None
        if ticket.department_id:
            from app.models.organization import Department
            dept_result = await db.execute(
                select(Department).where(Department.id == ticket.department_id)
            )
            dept = dept_result.scalar_one_or_none()
            if dept and dept.manager_id:
                dept_manager_id = dept.manager_id

        chief_compliance_id = None
        cc_result = await db.execute(
            select(Employee).where(Employee.position.ilike("%合规%"))
        )
        cc_officers = cc_result.scalars().all()
        if cc_officers:
            chief_compliance_id = cc_officers[0].id

        for title, order, total in approver_levels:
            approver_id = None
            if order == 1:
                approver_id = chief_compliance_id
            elif order == 2:
                approver_id = dept_manager_id
            else:
                approver_id = chief_compliance_id

            if not approver_id:
                approver_id = ticket.assigned_officer_id

            approver_name = None
            if approver_id:
                approver_result = await db.execute(
                    select(Employee).where(Employee.id == approver_id)
                )
                approver = approver_result.scalar_one_or_none()
                if approver:
                    approver_name = approver.name

            deadline = datetime.utcnow() + timedelta(hours=48 if severity == SeverityLevel.CRITICAL else 72)

            approval = ApprovalRecord(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                approval_type="disciplinary_review" if violation_result == ViolationResult.CONFIRMED else "conclusion_review",
                approval_order=order,
                total_levels=total,
                current_level=order,
                approver_id=approver_id,
                approver_name=approver_name,
                approver_title=title,
                status="pending",
                deadline=deadline,
            )
            db.add(approval)

        ticket.status = TicketStatus.UNDER_APPROVAL.value

        timeline = EventTimeline(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            timestamp=datetime.utcnow(),
            timeline_type="approval_initiated",
            title="审批流程启动",
            description=(
                f"审批等级: {len(approver_levels)}级\n"
                f"审批人: {', '.join(f'{a[0]} (第{a[1]}级)' for a in approver_levels)}"
            ),
            is_key_event=True,
        )
        db.add(timeline)

        log = SystemLog(
            id=uuid.uuid4(),
            log_level="INFO",
            action_type=LogActionType.APPROVAL_STARTED.value,
            target_type="investigation_ticket",
            target_id=ticket.id,
            target_name=ticket.ticket_number,
            action_details={
                "approval_levels": len(approver_levels),
                "violation_result": ticket.violation_result,
            },
            status="success",
        )
        db.add(log)

    async def _execute_disciplinary_action(
        self, db: AsyncSession, ticket: InvestigationTicket, approver_id: uuid.UUID
    ):
        if not ticket.disciplinary_action:
            return

        action_type = DisciplinaryAction(ticket.disciplinary_action)
        severity = SeverityLevel(ticket.severity)

        if action_type in [DisciplinaryAction.WARNING, DisciplinaryAction.SERIOUS_WARNING]:
            action_level = "warning"
        elif action_type in [DisciplinaryAction.DEMOTION, DisciplinaryAction.SALARY_REDUCTION]:
            action_level = "severe"
        elif action_type in [DisciplinaryAction.PERMISSION_FREEZE, DisciplinaryAction.TERMINATION]:
            action_level = "critical"
        else:
            action_level = "minor"

        expiry_date = None
        if action_type == DisciplinaryAction.WARNING:
            expiry_date = datetime.utcnow() + timedelta(days=180)
        elif action_type == DisciplinaryAction.SERIOUS_WARNING:
            expiry_date = datetime.utcnow() + timedelta(days=365)
        elif action_type == DisciplinaryAction.PERMISSION_FREEZE:
            expiry_date = datetime.utcnow() + timedelta(days=90)

        action_record = DisciplinaryActionRecord(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            employee_id=ticket.subject_employee_id,
            employee_name=ticket.subject_employee_name,
            department_id=ticket.department_id,
            department_name=ticket.department_name,
            action_type=action_type.value,
            action_level=action_level,
            severity_level=severity.value,
            action_date=datetime.utcnow(),
            effective_date=datetime.utcnow(),
            expiry_date=expiry_date,
            description=ticket.conclusion_summary,
            event_summary=ticket.title,
            action_details=ticket.conclusion_text,
            approval_id=approver_id,
            issued_by_id=approver_id,
            executed_at=datetime.utcnow(),
            status="executed",
        )
        db.add(action_record)

        if action_type == DisciplinaryAction.PERMISSION_FREEZE and ticket.subject_employee_id:
            emp_result = await db.execute(
                select(Employee).where(Employee.id == ticket.subject_employee_id)
            )
            emp = emp_result.scalar_one_or_none()
            if emp:
                current_restrictions = emp.permissions or []
                if "restricted" not in current_restrictions:
                    new_permissions = list(current_restrictions) + ["restricted"]
                    emp.permissions = new_permissions

        log = SystemLog(
            id=uuid.uuid4(),
            log_level="WARNING",
            action_type=LogActionType.ACTION_EXECUTED.value,
            target_type="disciplinary_action",
            target_id=action_record.id,
            target_name=f"{action_type.value}_{ticket.ticket_number}",
            user_id=approver_id,
            action_details={
                "ticket_id": str(ticket.id),
                "employee_id": str(ticket.subject_employee_id) if ticket.subject_employee_id else None,
                "action_type": action_type.value,
                "action_level": action_level,
                "expiry_date": expiry_date.isoformat() if expiry_date else None,
            },
            status="success",
        )
        db.add(log)

    async def _update_compliance_profile(
        self, db: AsyncSession, ticket: InvestigationTicket
    ):
        if not ticket.subject_employee_id:
            return

        profile_result = await db.execute(
            select(ComplianceProfile).where(
                ComplianceProfile.employee_id == ticket.subject_employee_id
            )
        )
        profile = profile_result.scalar_one_or_none()

        if not profile:
            profile = ComplianceProfile(
                id=uuid.uuid4(),
                employee_id=ticket.subject_employee_id,
                employee_name=ticket.subject_employee_name,
                department_id=ticket.department_id,
                department_name=ticket.department_name,
            )
            db.add(profile)
            await db.flush()

        old_values = {
            "compliance_score": profile.compliance_score,
            "risk_level": profile.risk_level,
            "total_events_count": profile.total_events_count,
            "confirmed_violations_count": profile.confirmed_violations_count,
            "pending_investigations_count": profile.pending_investigations_count,
        }

        profile.total_events_count = (profile.total_events_count or 0) + 1

        violation_result = ticket.violation_result

        if violation_result == ViolationResult.CONFIRMED.value:
            profile.confirmed_violations_count = (profile.confirmed_violations_count or 0) + 1
            profile.last_violation_date = datetime.utcnow()

            if ticket.disciplinary_action:
                penalty = self.SCORE_PENALTY_MAP.get(
                    DisciplinaryAction(ticket.disciplinary_action), 0
                )
                profile.compliance_score = max(0, (profile.compliance_score or 100) + penalty)

                existing_actions = profile.disciplinary_actions or []
                existing_actions.append({
                    "ticket_id": str(ticket.id),
                    "ticket_number": ticket.ticket_number,
                    "action": ticket.disciplinary_action,
                    "date": datetime.utcnow().isoformat(),
                    "severity": ticket.severity,
                })
                profile.disciplinary_actions = existing_actions
                profile.warnings_count = sum(
                    1 for a in existing_actions
                    if a.get("action") in [DisciplinaryAction.WARNING.value, DisciplinaryAction.SERIOUS_WARNING.value]
                )

                if ticket.disciplinary_action == DisciplinaryAction.TRAINING.value:
                    profile.next_training_due = datetime.utcnow() + timedelta(days=30)

            profile.total_investigation_hours = (profile.total_investigation_hours or 0) + (ticket.actual_hours or 0)

        elif violation_result == ViolationResult.FALSE_POSITIVE.value:
            profile.false_positives_count = (profile.false_positives_count or 0) + 1
            profile.compliance_score = min(100, (profile.compliance_score or 100) + 2)

        score = profile.compliance_score or 100
        if score >= 80:
            profile.risk_level = "low"
        elif score >= 50:
            profile.risk_level = "medium"
        else:
            profile.risk_level = "high"

        active_tickets = profile.active_tickets or []
        active_tickets = [t for t in active_tickets if t != ticket.id]
        profile.active_tickets = active_tickets
        profile.pending_investigations_count = len(active_tickets)

        history_entry = ComplianceProfileHistory(
            id=uuid.uuid4(),
            profile_id=profile.id,
            change_type="investigation_result",
            field_changed="overall",
            old_value=old_values,
            new_value={
                "compliance_score": profile.compliance_score,
                "risk_level": profile.risk_level,
                "total_events_count": profile.total_events_count,
                "confirmed_violations_count": profile.confirmed_violations_count,
                "violation_result": violation_result,
            },
            related_ticket_id=ticket.id,
            description=f"工单{ticket.ticket_number}结案: {violation_result}, 处分: {ticket.disciplinary_action or '无'}",
            created_at=datetime.utcnow(),
        )
        db.add(history_entry)

        closed_tickets = set()
        if profile.closed_tickets_count is not None:
            profile.closed_tickets_count = profile.closed_tickets_count + 1
        else:
            profile.closed_tickets_count = 1

    @staticmethod
    async def _get_ticket(db: AsyncSession, ticket_id: uuid.UUID) -> InvestigationTicket:
        result = await db.execute(
            select(InvestigationTicket).where(InvestigationTicket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket:
            raise ValueError(f"Ticket not found: {ticket_id}")
        return ticket

    @staticmethod
    def _generate_conclusion_summary(
        ticket: InvestigationTicket,
        conclusion: InvestigationConclusion,
        violation_result: ViolationResult,
        disciplinary_action: Optional[DisciplinaryAction],
    ) -> str:
        cn_map = {
            InvestigationConclusion.GUILTY: "确认违规",
            InvestigationConclusion.NOT_GUILTY: "排除违规",
            InvestigationConclusion.INSUFFICIENT_EVIDENCE: "证据不足",
            InvestigationConclusion.FALSE_ALARM: "误报",
        }
        result_cn = {
            ViolationResult.CONFIRMED: "确认违规",
            ViolationResult.UNCONFIRMED: "无法确认",
            ViolationResult.FALSE_POSITIVE: "误报排除",
            ViolationResult.PENDING: "待认定",
        }
        action_cn = {
            DisciplinaryAction.WARNING: "书面警告",
            DisciplinaryAction.SERIOUS_WARNING: "记过处分",
            DisciplinaryAction.DEMOTION: "降级处理",
            DisciplinaryAction.SALARY_REDUCTION: "降薪处理",
            DisciplinaryAction.PERMISSION_FREEZE: "冻结权限",
            DisciplinaryAction.TERMINATION: "解除劳动合同",
            DisciplinaryAction.TRAINING: "强制合规培训",
            DisciplinaryAction.NO_ACTION: "免于处罚",
        }

        summary = f"调查结论: {cn_map.get(conclusion, conclusion.value)}\n"
        summary += f"违规认定: {result_cn.get(violation_result, violation_result.value)}\n"

        if violation_result == ViolationResult.CONFIRMED and disciplinary_action:
            summary += f"纪律处分: {action_cn.get(disciplinary_action, disciplinary_action.value)}\n"

        return summary

    @staticmethod
    async def _log_action(
        db: AsyncSession,
        action_type: str,
        ticket: InvestigationTicket,
        user_id: uuid.UUID,
        details: Dict[str, Any],
    ):
        log = SystemLog(
            id=uuid.uuid4(),
            log_level="INFO",
            action_type=action_type,
            target_type="investigation_ticket",
            target_id=ticket.id,
            target_name=ticket.ticket_number,
            user_id=user_id,
            action_details=details,
            status="success",
        )
        db.add(log)
