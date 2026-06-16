from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import uuid
from collections import defaultdict
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import logger
from app.core.constants import (
    SeverityLevel, EventType, TicketStatus,
    SEVERITY_TIME_LIMIT, SEVERITY_LEVEL_MAP,
    EventTypeCN
)
from app.core.database import get_db_context
from app.models.investigation import (
    ComplianceEvent, InvestigationTicket, EvidenceItem
)
from app.models.organization import Employee, InvestigationOfficer, Department
from app.models.compliance import EventTimeline, SystemLog, LogActionType


class EventClassificationService:
    EVENT_TYPE_SEVERITY_BOOST = {
        EventType.FRAUD: 20,
        EventType.DATA_LEAK: 25,
        EventType.HARASSMENT: 30,
        EventType.THEFT: 25,
        EventType.DISCRIMINATION: 25,
    }

    MULTI_SOURCE_BOOST = 15
    HISTORY_PENALTY_PER_VIOLATION = 10
    DEADLINE_PENALTY_THRESHOLD = 50

    def __init__(self):
        self.logger = logger.bind(module="EventClassificationService")

    async def classify_event(self, event: ComplianceEvent) -> ComplianceEvent:
        final_score = event.risk_score

        event_type = EventType(event.event_type)
        if event_type in self.EVENT_TYPE_SEVERITY_BOOST:
            final_score += self.EVENT_TYPE_SEVERITY_BOOST[event_type]

        source_count = len(event.matched_data_sources or [])
        if source_count >= 2:
            final_score += self.MULTI_SOURCE_BOOST

        if event.subject_employee_id:
            async with get_db_context() as db:
                from app.models.compliance import ComplianceProfile
                profile_result = await db.execute(
                    select(ComplianceProfile).where(
                        ComplianceProfile.employee_id == event.subject_employee_id
                    )
                )
                profile = profile_result.scalar_one_or_none()
                if profile and profile.confirmed_violations_count > 0:
                    final_score += min(
                        profile.confirmed_violations_count * self.HISTORY_PENALTY_PER_VIOLATION,
                        50
                    )

        final_score = min(100, max(0, final_score))

        if final_score >= 80:
            severity = SeverityLevel.CRITICAL
        elif final_score >= 50:
            severity = SeverityLevel.IMPORTANT
        else:
            severity = SeverityLevel.GENERAL

        event.severity = severity.value
        event.risk_score = final_score

        priority_score = (
            SEVERITY_LEVEL_MAP[severity] * 1000 +
            final_score +
            (100 if event.confidence >= 0.9 else 50)
        )
        event.extra_metadata = event.extra_metadata or {}
        event.extra_metadata["final_score"] = final_score
        event.extra_metadata["priority_score"] = priority_score

        return event


class TicketGenerationService:
    def __init__(self):
        self.logger = logger.bind(module="TicketGenerationService")
        self._ticket_counter = defaultdict(int)

    async def create_tickets_from_events(
        self, events: List[ComplianceEvent]
    ) -> List[InvestigationTicket]:
        tickets: List[InvestigationTicket] = []
        event_groups = self._group_events(events)

        async with get_db_context() as db:
            for group_key, group_events in event_groups.items():
                try:
                    ticket = await self._create_single_ticket(db, group_events)
                    if ticket:
                        tickets.append(ticket)
                        for evt in group_events:
                            evt.ticket_id = ticket.id
                            evt.status = "ticket_created"
                except Exception as e:
                    self.logger.error(
                        "Failed to create ticket for event group",
                        group_key=group_key,
                        error=str(e)
                    )
                    continue

        self.logger.info(
            "Ticket generation complete",
            events_processed=len(events),
            tickets_created=len(tickets)
        )
        return tickets

    def _group_events(self, events: List[ComplianceEvent]) -> Dict[str, List[ComplianceEvent]]:
        groups: Dict[str, List[ComplianceEvent]] = defaultdict(list)

        for event in events:
            severity = event.severity
            emp_id = str(event.subject_employee_id) if event.subject_employee_id else "unknown"
            event_date = event.event_time.strftime("%Y%m%d") if event.event_time else datetime.utcnow().strftime("%Y%m%d")
            event_type = event.event_type

            if severity == SeverityLevel.CRITICAL.value:
                key = f"critical_{emp_id}_{event_date}_{event.id}"
            else:
                key = f"{severity}_{emp_id}_{event_date}_{event_type}"

            groups[key].append(event)

        return groups

    async def _create_single_ticket(
        self, db: AsyncSession, events: List[ComplianceEvent]
    ) -> Optional[InvestigationTicket]:
        if not events:
            return None

        primary = self._select_primary_event(events)
        severity = SeverityLevel(primary.severity)
        deadline = datetime.utcnow() + SEVERITY_TIME_LIMIT[severity]

        date_str = datetime.utcnow().strftime("%Y%m%d")
        counter_key = f"{date_str}_{severity.value}"
        self._ticket_counter[counter_key] += 1
        seq = self._ticket_counter[counter_key]
        ticket_number = f"TKT-{date_str}-{severity.value[:3].upper()}-{seq:05d}"

        max_risk = max(e.risk_score for e in events)
        sources = set()
        for e in events:
            sources.update(e.matched_data_sources or [])

        event_type_cn = EventTypeCN[EventType(primary.event_type).name].value
        severity_cn = {"general": "一般", "important": "重要", "critical": "重大"}.get(severity.value, "")
        title = f"[{severity_cn}]{primary.subject_employee_name or '匿名员工'}-{event_type_cn}调查"

        descriptions = []
        for e in events:
            ev_time = e.event_time.strftime("%Y-%m-%d %H:%M") if e.event_time else "未知时间"
            sources_str = ",".join(e.matched_data_sources or [])
            descriptions.append(f"• [{ev_time}] [{sources_str}] {e.title}")

        description = "关联违规事件明细：\n" + "\n".join(descriptions)

        priority_score = (
            SEVERITY_LEVEL_MAP[severity] * 10000 +
            max_risk * 100 +
            len(events) * 10
        )

        ticket = InvestigationTicket(
            id=uuid.uuid4(),
            ticket_number=ticket_number,
            title=title,
            description=description,
            event_type=primary.event_type,
            severity=severity.value,
            status=TicketStatus.PENDING.value,
            subject_employee_id=primary.subject_employee_id,
            subject_employee_name=primary.subject_employee_name,
            department_id=primary.subject_department_id,
            department_name=primary.subject_department_name,
            deadline=deadline,
            priority_score=priority_score,
            tags=[f"evt_{EventType(e.event_type).value}" for e in events] + list(sources),
        )
        db.add(ticket)
        await db.flush()

        await self._create_ticket_timeline(db, ticket, events)
        await self._log_system_action(db, ticket, events)

        return ticket

    @staticmethod
    def _select_primary_event(events: List[ComplianceEvent]) -> ComplianceEvent:
        severity_order = {
            SeverityLevel.CRITICAL.value: 3,
            SeverityLevel.IMPORTANT.value: 2,
            SeverityLevel.GENERAL.value: 1,
        }
        return max(events, key=lambda e: (
            severity_order.get(e.severity, 0),
            e.risk_score,
            e.confidence,
            len(e.matched_data_sources or []),
        ))

    async def _create_ticket_timeline(
        self, db: AsyncSession, ticket: InvestigationTicket, events: List[ComplianceEvent]
    ):
        for event in events:
            timeline = EventTimeline(
                id=uuid.uuid4(),
                event_id=event.id,
                ticket_id=ticket.id,
                employee_id=event.subject_employee_id,
                employee_name=event.subject_employee_name,
                timestamp=event.event_time or event.detected_at,
                timeline_type=event.event_type,
                data_source=event.matched_data_sources[0] if event.matched_data_sources else None,
                title=event.title,
                description=event.evidence_summary or event.description,
                source_record_id=(event.raw_data_record_ids[0]
                                 if event.raw_data_record_ids else None),
                is_key_event=(event.severity == SeverityLevel.CRITICAL.value),
            )
            db.add(timeline)

    async def _log_system_action(
        self, db: AsyncSession, ticket: InvestigationTicket, events: List[ComplianceEvent]
    ):
        log = SystemLog(
            id=uuid.uuid4(),
            log_level="INFO",
            action_type=LogActionType.TICKET_CREATED.value,
            target_type="investigation_ticket",
            target_id=ticket.id,
            target_name=ticket.ticket_number,
            action_details={
                "severity": ticket.severity,
                "event_type": ticket.event_type,
                "events_count": len(events),
                "event_codes": [e.event_code for e in events],
                "deadline": ticket.deadline.isoformat() if ticket.deadline else None,
            },
            status="success",
        )
        db.add(log)


class OfficerAssignmentService:
    def __init__(self):
        self.logger = logger.bind(module="OfficerAssignmentService")

    EVENT_TYPE_SPECIALIZATION_MAP = {
        EventType.FRAUD: "财务调查",
        EventType.DATA_LEAK: "信息安全",
        EventType.UNAUTHORIZED_ACCESS: "安全审计",
        EventType.HARASSMENT: "人力资源",
        EventType.DISCRIMINATION: "人力资源",
        EventType.CONFLICT_OF_INTEREST: "合规审计",
        EventType.THEFT: "安全审计",
        EventType.POLICY_VIOLATION: "合规审计",
        EventType.SUSPICIOUS_COMMUNICATION: "信息安全",
        EventType.ABNORMAL_BEHAVIOR: "综合调查",
        EventType.OTHER: "综合调查",
    }

    LOAD_BALANCE_WEIGHT = 0.3
    SPECIALIZATION_WEIGHT = 0.4
    DEPARTMENT_WEIGHT = 0.2
    AVAILABILITY_WEIGHT = 0.1

    async def assign_officers(self, tickets: List[InvestigationTicket]) -> List[InvestigationTicket]:
        if not tickets:
            return tickets

        assigned: List[InvestigationTicket] = []

        async with get_db_context() as db:
            officers_result = await db.execute(
                select(InvestigationOfficer).where(
                    InvestigationOfficer.is_available == True
                )
            )
            officers = officers_result.scalars().all()

            if not officers:
                self.logger.warning("No available investigation officers found")
                return tickets

            workload_result = await db.execute(
                select(
                    InvestigationTicket.assigned_officer_id,
                    func.count(InvestigationTicket.id)
                ).where(
                    InvestigationTicket.assigned_officer_id.isnot(None),
                    InvestigationTicket.status.in_([
                        TicketStatus.ASSIGNED.value,
                        TicketStatus.UNDER_INVESTIGATION.value,
                        TicketStatus.EVIDENCE_COLLECTED.value,
                    ])
                ).group_by(InvestigationTicket.assigned_officer_id)
            )
            current_workload = dict(workload_result.all())

            sorted_tickets = sorted(
                tickets,
                key=lambda t: (-t.priority_score, t.created_at)
            )

            for ticket in sorted_tickets:
                try:
                    best_officer = self._select_best_officer(
                        ticket, officers, current_workload
                    )
                    if best_officer:
                        await self._assign_ticket_to_officer(db, ticket, best_officer)
                        assigned.append(ticket)
                        emp_id_str = str(best_officer.employee_id)
                        current_workload[emp_id_str] = current_workload.get(emp_id_str, 0) + 1
                    else:
                        self.logger.warning(
                            "Could not assign officer",
                            ticket_number=ticket.ticket_number
                        )
                except Exception as e:
                    self.logger.error(
                        "Failed to assign ticket",
                        ticket_number=ticket.ticket_number,
                        error=str(e)
                    )

        self.logger.info(
            "Officer assignment complete",
            tickets_assigned=len(assigned),
            total_tickets=len(tickets)
        )
        return assigned

    def _select_best_officer(
        self,
        ticket: InvestigationTicket,
        officers: List[InvestigationOfficer],
        workload: Dict[str, int]
    ) -> Optional[InvestigationOfficer]:
        event_type = EventType(ticket.event_type)
        required_specialization = self.EVENT_TYPE_SPECIALIZATION_MAP.get(event_type, "综合调查")

        scores: List[Tuple[float, InvestigationOfficer]] = []

        for officer in officers:
            current_load = workload.get(str(officer.employee_id), 0)
            if current_load >= officer.max_ticket_capacity:
                continue

            load_ratio = current_load / max(officer.max_ticket_capacity, 1)
            load_score = (1 - load_ratio) * self.LOAD_BALANCE_WEIGHT

            specializations = officer.specializations or []
            if required_specialization in specializations:
                spec_score = 1.0 * self.SPECIALIZATION_WEIGHT
            elif "综合调查" in specializations:
                spec_score = 0.5 * self.SPECIALIZATION_WEIGHT
            else:
                spec_score = 0.0

            dept_score = 0.0
            if ticket.department_id and officer.departments_covered:
                if ticket.department_id in officer.departments_covered:
                    dept_score = self.DEPARTMENT_WEIGHT

            availability_score = 1.0 if officer.is_available else 0.0
            availability_score *= self.AVAILABILITY_WEIGHT

            total_score = load_score + spec_score + dept_score + availability_score
            if ticket.severity == SeverityLevel.CRITICAL.value:
                if required_specialization in specializations:
                    total_score += 0.2

            scores.append((total_score, officer))

        if not scores:
            return None

        scores.sort(key=lambda x: -x[0])
        return scores[0][1]

    async def _assign_ticket_to_officer(
        self, db: AsyncSession, ticket: InvestigationTicket, officer: InvestigationOfficer
    ):
        ticket.assigned_officer_id = officer.employee_id
        ticket.status = TicketStatus.ASSIGNED.value
        ticket.assigned_at = datetime.utcnow()

        emp_result = await db.execute(
            select(Employee).where(Employee.id == officer.employee_id)
        )
        employee = emp_result.scalar_one_or_none()
        if employee:
            ticket.assigned_officer_name = employee.name

        officer.current_ticket_count = (officer.current_ticket_count or 0) + 1

        log = SystemLog(
            id=uuid.uuid4(),
            log_level="INFO",
            action_type=LogActionType.TICKET_ASSIGNED.value,
            target_type="investigation_ticket",
            target_id=ticket.id,
            target_name=ticket.ticket_number,
            user_id=officer.employee_id,
            user_name=employee.name if employee else None,
            action_details={
                "officer_id": str(officer.employee_id),
                "officer_specializations": officer.specializations,
            },
            status="success",
        )
        db.add(log)
