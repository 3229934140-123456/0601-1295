from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import uuid
import hashlib
import json
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import logger
from app.core.constants import (
    SeverityLevel, EventType, TicketStatus, LogActionType,
    DataSourceType
)
from app.core.database import get_db_context
from app.models.compliance import EmployeeReport, SystemLog
from app.models.investigation import InvestigationTicket, ComplianceEvent
from app.models.organization import Employee


class EmployeeReportService:
    DEDUPLICATION_TIME_WINDOW = timedelta(days=7)
    SIMILARITY_THRESHOLD = 0.7

    def __init__(self):
        self.logger = logger.bind(module="EmployeeReportService")
        self._report_counter = 0

    async def submit_report(
        self,
        reporter_employee_id: Optional[uuid.UUID],
        is_anonymous: bool,
        reported_employee_name: str,
        reported_event_type: EventType,
        event_date: Optional[datetime],
        title: str,
        description: str,
        reported_severity: Optional[SeverityLevel] = None,
        reporter_name: Optional[str] = None,
        reporter_contact: Optional[str] = None,
        key_details: Optional[List[str]] = None,
        witness_names: Optional[List[str]] = None,
        evidence_description: Optional[str] = None,
        evidence_files: Optional[List[str]] = None,
        impact_assessment: Optional[str] = None,
        event_location: Optional[str] = None,
        reported_employee_id: Optional[uuid.UUID] = None,
    ) -> EmployeeReport:
        async with get_db_context() as db:
            self._report_counter += 1
            report_number = f"RPT-{datetime.utcnow().strftime('%Y%m%d')}-{self._report_counter:05d}"

            dedupe_hash = self._compute_dedupe_hash(
                reported_employee_name=reported_employee_name,
                reported_event_type=reported_event_type,
                event_date=event_date,
                title=title,
                description=description,
            )

            report = EmployeeReport(
                id=uuid.uuid4(),
                report_number=report_number,
                reporter_employee_id=None if is_anonymous else reporter_employee_id,
                reporter_name="匿名" if is_anonymous else reporter_name,
                reporter_contact=None if is_anonymous else reporter_contact,
                is_anonymous=is_anonymous,
                reported_employee_id=reported_employee_id,
                reported_employee_name=reported_employee_name,
                reported_event_type=reported_event_type.value,
                reported_severity=reported_severity.value if reported_severity else None,
                event_date=event_date,
                event_location=event_location,
                title=title,
                description=description,
                key_details=key_details or [],
                witness_names=witness_names or [],
                evidence_description=evidence_description,
                evidence_files=evidence_files or [],
                impact_assessment=impact_assessment,
                status="submitted",
                deduplication_hash=dedupe_hash,
                priority=self._calculate_priority(reported_severity, reported_event_type),
                created_at=datetime.utcnow(),
            )

            dup_result = await self._check_duplicates(db, report)
            if dup_result:
                is_dup, matched_report = dup_result
                if is_dup:
                    report.is_duplicate = True
                    report.duplicate_of_report_id = matched_report.id
                    report.status = "duplicate"

                    if matched_report.merged_ticket_id:
                        report.merged_ticket_id = matched_report.merged_ticket_id
                    self.logger.info(
                        "Employee report detected as duplicate",
                        report_number=report_number,
                        matched_with=matched_report.report_number
                    )

            db.add(report)

            log_data = {
                "report_number": report_number,
                "is_anonymous": is_anonymous,
                "reported_event_type": reported_event_type.value,
                "is_duplicate": report.is_duplicate,
                "matched_report": str(report.duplicate_of_report_id) if report.duplicate_of_report_id else None,
            }

            if not is_anonymous and reporter_employee_id:
                emp_result = await db.execute(
                    select(Employee).where(Employee.id == reporter_employee_id)
                )
                emp = emp_result.scalar_one_or_none()
                log_data["reporter_employee_id"] = str(reporter_employee_id)
                log_data["reporter_employee_name"] = emp.name if emp else None

            log = SystemLog(
                id=uuid.uuid4(),
                log_level="INFO",
                action_type="employee_report_submitted",
                target_type="employee_report",
                target_id=report.id,
                target_name=report_number,
                action_details=log_data,
                status="success",
            )
            db.add(log)

            self.logger.info(
                "Employee report submitted",
                report_number=report_number,
                is_anonymous=is_anonymous,
                is_duplicate=report.is_duplicate,
                event_type=reported_event_type.value
            )
            return report

    async def process_report(
        self,
        report_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        action: str,
        review_notes: Optional[str] = None,
        target_ticket_id: Optional[uuid.UUID] = None,
    ) -> EmployeeReport:
        async with get_db_context() as db:
            report_result = await db.execute(
                select(EmployeeReport).where(EmployeeReport.id == report_id)
            )
            report = report_result.scalar_one_or_none()
            if not report:
                raise ValueError(f"Report not found: {report_id}")

            reviewer_result = await db.execute(
                select(Employee).where(Employee.id == reviewer_id)
            )
            reviewer = reviewer_result.scalar_one_or_none()
            reviewer_name = reviewer.name if reviewer else None

            if action == "merge":
                if target_ticket_id:
                    report.merged_ticket_id = target_ticket_id
                else:
                    ticket = await self._create_ticket_from_report(db, report)
                    report.merged_ticket_id = ticket.id
                report.status = "merged"
                report.reviewed_by_id = reviewer_id
                report.reviewed_at = datetime.utcnow()
                report.review_notes = review_notes

            elif action == "dismiss":
                report.status = "dismissed"
                report.reviewed_by_id = reviewer_id
                report.reviewed_at = datetime.utcnow()
                report.review_notes = review_notes or "经审查不构成违规事件"

            elif action == "flag_for_review":
                report.status = "pending_review"
                report.reviewed_by_id = reviewer_id
                report.reviewed_at = datetime.utcnow()
                report.review_notes = review_notes

            else:
                raise ValueError(f"Invalid action: {action}")

            log = SystemLog(
                id=uuid.uuid4(),
                log_level="INFO",
                action_type="employee_report_processed",
                target_type="employee_report",
                target_id=report.id,
                target_name=report.report_number,
                user_id=reviewer_id,
                user_name=reviewer_name,
                action_details={
                    "action": action,
                    "review_notes": review_notes,
                    "merged_ticket_id": str(report.merged_ticket_id) if report.merged_ticket_id else None,
                },
                status="success",
            )
            db.add(log)

            self.logger.info(
                "Employee report processed",
                report_number=report.report_number,
                action=action,
                reviewer=str(reviewer_id)
            )
            return report

    async def _check_duplicates(
        self, db: AsyncSession, report: EmployeeReport
    ) -> Optional[Tuple[bool, Optional[EmployeeReport]]]:
        window_start = datetime.utcnow() - self.DEDUPLICATION_TIME_WINDOW

        query = select(EmployeeReport).where(
            EmployeeReport.created_at >= window_start,
            EmployeeReport.id != report.id
        )
        result = await db.execute(query)
        existing_reports = result.scalars().all()

        hash_matches = [
            r for r in existing_reports
            if r.deduplication_hash == report.deduplication_hash
        ]
        if hash_matches:
            return (True, hash_matches[0])

        for existing in existing_reports:
            similarity = self._calculate_similarity(report, existing)
            if similarity >= self.SIMILARITY_THRESHOLD:
                return (True, existing)

        event_matches_query = select(ComplianceEvent).where(
            ComplianceEvent.detected_at >= window_start,
            ComplianceEvent.event_type == report.reported_event_type,
        )

        if report.reported_employee_id:
            event_matches_query = event_matches_query.where(
                ComplianceEvent.subject_employee_id == report.reported_employee_id
            )

        event_result = await db.execute(event_matches_query)
        matching_events = event_result.scalars().all()
        if matching_events:
            event = matching_events[0]
            if event.ticket_id:
                pseudo_report = EmployeeReport(
                    id=event.id,
                    report_number=f"EVT-{event.event_code}",
                    merged_ticket_id=event.ticket_id,
                )
                return (True, pseudo_report)

        return None

    async def _create_ticket_from_report(
        self, db: AsyncSession, report: EmployeeReport
    ) -> InvestigationTicket:
        severity = SeverityLevel(report.reported_severity) if report.reported_severity else SeverityLevel.IMPORTANT
        from app.core.constants import SEVERITY_TIME_LIMIT
        deadline = datetime.utcnow() + SEVERITY_TIME_LIMIT[severity]

        from collections import defaultdict
        counter_key = f"manual_{datetime.utcnow().strftime('%Y%m%d')}_{severity.value}"
        import app.workflows.ticket_manager as tm
        tm_service = tm.TicketGenerationService()
        if not hasattr(tm_service, '_EmployeeReportService_ticket_counter'):
            setattr(tm_service, '_EmployeeReportService_ticket_counter', defaultdict(int))
        counter = getattr(tm_service, '_EmployeeReportService_ticket_counter')
        counter[counter_key] += 1
        seq = counter[counter_key]

        ticket_number = f"TKT-MNL-{datetime.utcnow().strftime('%Y%m%d')}-{severity.value[:3].upper()}-{seq:05d}"

        severity_cn = {"general": "一般", "important": "重要", "critical": "重大"}.get(severity.value, "")
        event_type_cn = self._event_type_cn(EventType(report.reported_event_type))
        title = f"[{severity_cn}][员工申报]{report.reported_employee_name}-{event_type_cn}"

        ticket = InvestigationTicket(
            id=uuid.uuid4(),
            ticket_number=ticket_number,
            title=title,
            description=f"来源: 员工主动申报\n申报标题: {report.title}\n详细描述:\n{report.description}",
            event_type=report.reported_event_type,
            severity=severity.value,
            status=TicketStatus.PENDING.value,
            subject_employee_id=report.reported_employee_id,
            subject_employee_name=report.reported_employee_name,
            deadline=deadline,
            priority_score=report.priority or 500,
            tags=[f"source_employee_report", f"report_{report.report_number}"],
        )
        db.add(ticket)
        await db.flush()

        from app.models.compliance import EventTimeline
        timeline = EventTimeline(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            timestamp=datetime.utcnow(),
            timeline_type="report_submitted",
            title="员工举报/申报",
            description=(
                f"申报编号: {report.report_number}\n"
                f"申报人: {report.reporter_name or '匿名'}\n"
                f"事件类型: {report.reported_event_type}\n"
                f"事件时间: {report.event_date.strftime('%Y-%m-%d %H:%M') if report.event_date else '未知'}\n"
                f"事件地点: {report.event_location or '未提供'}\n"
                f"关键细节: {'; '.join(report.key_details) if report.key_details else '无'}"
            ),
            is_key_event=True,
        )
        db.add(timeline)

        event = ComplianceEvent(
            id=uuid.uuid4(),
            event_code=f"EVT-RPT-{report.report_number}",
            event_type=report.reported_event_type,
            severity=severity.value,
            subject_employee_id=report.reported_employee_id,
            subject_employee_name=report.reported_employee_name,
            detected_at=datetime.utcnow(),
            event_time=report.event_date,
            title=f"[员工申报]{report.title}",
            description=report.description,
            evidence_summary=report.evidence_description,
            risk_score=report.priority or 60,
            confidence=0.85,
            matched_data_sources=[DataSourceType.EMPLOYEE_REPORT.value],
            status="ticket_created",
            ticket_id=ticket.id,
            deduplication_hash=hashlib.sha256(f"report_{report.id}".encode()).hexdigest(),
            extra_metadata={
                "source": "employee_report",
                "report_id": str(report.id),
                "report_number": report.report_number,
            },
        )
        db.add(event)

        return ticket

    @staticmethod
    def _compute_dedupe_hash(
        reported_employee_name: str,
        reported_event_type: EventType,
        event_date: Optional[datetime],
        title: str,
        description: str,
    ) -> str:
        date_str = event_date.strftime("%Y%m%d") if event_date else "unknown"
        key_text = "|".join([
            reported_employee_name.strip().lower(),
            reported_event_type.value,
            date_str,
            title.strip().lower()[:100],
            description.strip().lower()[:300],
        ])
        return hashlib.sha256(key_text.encode()).hexdigest()

    @staticmethod
    def _calculate_similarity(
        report1: EmployeeReport, report2: EmployeeReport
    ) -> float:
        score = 0.0
        total_weights = 0.0

        if report1.reported_event_type == report2.reported_event_type:
            score += 0.3
        total_weights += 0.3

        name1 = (report1.reported_employee_name or "").lower()
        name2 = (report2.reported_employee_name or "").lower()
        if name1 and name2 and (name1 in name2 or name2 in name1):
            score += 0.25
        total_weights += 0.25

        if report1.event_date and report2.event_date:
            date_diff = abs((report1.event_date - report2.event_date).total_seconds())
            if date_diff < 86400:
                score += 0.2 * (1 - date_diff / 86400)
        total_weights += 0.2

        desc1_words = set((report1.description or "").lower().split())
        desc2_words = set((report2.description or "").lower().split())
        if desc1_words and desc2_words:
            common = len(desc1_words & desc2_words)
            total = len(desc1_words | desc2_words)
            jaccard = common / total if total > 0 else 0
            score += 0.25 * jaccard
        total_weights += 0.25

        return score / total_weights if total_weights > 0 else 0.0

    @staticmethod
    def _calculate_priority(
        reported_severity: Optional[SeverityLevel],
        event_type: EventType,
    ) -> int:
        priority = 0
        if reported_severity:
            severity_map = {
                SeverityLevel.CRITICAL: 900,
                SeverityLevel.IMPORTANT: 600,
                SeverityLevel.GENERAL: 300,
            }
            priority += severity_map.get(reported_severity, 300)
        else:
            priority += 500

        high_priority_types = {
            EventType.FRAUD, EventType.HARASSMENT, EventType.THEFT,
            EventType.DATA_LEAK, EventType.DISCRIMINATION
        }
        if event_type in high_priority_types:
            priority += 100

        return min(1000, priority)

    @staticmethod
    def _event_type_cn(event_type: EventType) -> str:
        cn_map = {
            EventType.DATA_LEAK: "数据泄露",
            EventType.UNAUTHORIZED_ACCESS: "未授权访问",
            EventType.FRAUD: "财务欺诈",
            EventType.CONFLICT_OF_INTEREST: "利益冲突",
            EventType.HARASSMENT: "职场骚扰",
            EventType.DISCRIMINATION: "歧视行为",
            EventType.THEFT: "盗窃行为",
            EventType.POLICY_VIOLATION: "违反制度",
            EventType.SUSPICIOUS_COMMUNICATION: "可疑通讯",
            EventType.ABNORMAL_BEHAVIOR: "异常行为",
            EventType.OTHER: "其他违规",
        }
        return cn_map.get(event_type, "违规行为")
