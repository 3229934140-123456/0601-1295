from __future__ import annotations
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any

from app.core.config import settings
from app.core.logging_config import logger
from app.core.database import get_db_context
from app.data_collectors import get_all_collectors
from app.detection_engine import ViolationDetectionEngine
from app.workflows import (
    EventClassificationService,
    TicketGenerationService,
    OfficerAssignmentService,
    TicketEscalationService,
    EvidenceCollectionService,
)
from app.services import (
    ReportService,
    NotificationService,
)
from app.core.constants import SeverityLevel

try:
    from app.core.celery_app import celery_app
    from celery import Celery
    CELERY_AVAILABLE = True
except ImportError:
    celery_app = None
    CELERY_AVAILABLE = False


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        return asyncio.ensure_future(coro)
    return loop.run_until_complete(coro)


class _MockTaskResult:
    def __init__(self, result=None):
        self.id = "sync-task-" + str(id(result))[:36]
        self._result = result

    @property
    def result(self):
        return self._result

    def get(self, timeout=None):
        return self._result

    def ready(self):
        return True


def _make_sync_task(func):
    def wrapper(*args, **kwargs):
        return _MockTaskResult(func(*args, **kwargs))

    wrapper.delay = wrapper
    wrapper.s = lambda *a, **kw: lambda: func(*a, **kw)
    return wrapper


def _collect_all_sources_sync(hours: int = 24):
    logger.info("Starting data collection from all sources", lookback_hours=hours)
    results: Dict[str, int] = {}

    async def _collect():
        collectors = get_all_collectors()
        for collector in collectors:
            try:
                count = await collector.collect_last_hours(hours)
                results[collector.data_source_type.value] = count
                logger.info(
                    "Source collection complete",
                    source=collector.data_source_type.value,
                    records=count
                )
            except Exception as e:
                logger.error(
                    "Source collection failed",
                    source=collector.data_source_type.value,
                    error=str(e)
                )
                results[collector.data_source_type.value] = -1
        return results

    return _run_async(_collect())


def _collect_single_source_sync(source_type: str, hours: int = 24):
    logger.info("Collecting single source", source=source_type, hours=hours)

    async def _collect():
        collectors = get_all_collectors()
        for collector in collectors:
            if collector.data_source_type.value == source_type:
                count = await collector.collect_last_hours(hours)
                return {"source": source_type, "records": count}
        return {"source": source_type, "error": "not found"}

    return _run_async(_collect())


def _run_violation_detection_sync(lookback_hours: int = 24):
    logger.info("Starting violation detection", lookback_hours=lookback_hours)

    async def _detect():
        engine = ViolationDetectionEngine()
        events = await engine.detect_violations(lookback_hours=lookback_hours)
        logger.info(
            "Violation detection complete",
            events_found=len(events)
        )
        return {
            "events_detected": len(events),
            "event_codes": [e.event_code for e in events[:100]],
        }

    result = _run_async(_detect())

    if result and result.get("events_detected", 0) > 0:
        _classify_and_create_tickets_sync()

    return result


def _classify_and_create_tickets_sync():
    logger.info("Starting ticket classification and creation")

    async def _process():
        from sqlalchemy import select, and_
        from app.models.investigation import ComplianceEvent

        async with get_db_context() as db:
            pending_events = await db.execute(
                select(ComplianceEvent).where(
                    and_(
                        ComplianceEvent.ticket_id.is_(None),
                        ComplianceEvent.is_duplicate == False,
                    )
                )
            )
            events = pending_events.scalars().all()

        if not events:
            return {"processed": 0, "tickets_created": 0}

        classifier = EventClassificationService()
        for event in events:
            event = await classifier.classify_event(event)

        ticket_service = TicketGenerationService()
        tickets = await ticket_service.create_tickets_from_events(events)

        if tickets:
            assign_service = OfficerAssignmentService()
            assigned = await assign_service.assign_officers(tickets)

            notif_service = NotificationService()
            async with get_db_context() as db:
                from app.models.investigation import ComplianceEvent as CE
                from app.models.organization import Employee
                for t in assigned:
                    if t.severity == SeverityLevel.CRITICAL.value:
                        evts_result = await db.execute(
                            select(CE).where(
                                CE.ticket_id == t.id
                            )
                        )
                        for ev in evts_result.scalars().all():
                            await notif_service.notify_critical_event(ev)

                    if t.assigned_officer_id:
                        off_result = await db.execute(
                            select(Employee).where(Employee.id == t.assigned_officer_id)
                        )
                        officer = off_result.scalar_one_or_none()
                        if officer:
                            await notif_service.notify_ticket_assignment(t, officer)

        return {
            "events_processed": len(events),
            "tickets_created": len(tickets),
            "officers_assigned": len([t for t in tickets if t.assigned_officer_id]),
        }

    return _run_async(_process())


def _process_escalations_sync():
    logger.info("Processing overdue ticket escalations")

    async def _process():
        escalation_service = TicketEscalationService()
        stats = await escalation_service.check_and_process_overdue()
        return stats

    return _run_async(_process())


def _collect_ticket_evidence_sync(ticket_id: str):
    logger.info("Collecting evidence for ticket", ticket_id=ticket_id)

    async def _collect():
        import uuid
        evidence_service = EvidenceCollectionService()
        package = await evidence_service.collect_evidence_for_ticket(
            uuid.UUID(ticket_id)
        )
        return {
            "ticket_id": ticket_id,
            "package_code": package.package_code,
            "evidence_count": package.evidence_count,
            "status": package.status,
        }

    return _run_async(_collect())


def _generate_daily_report_sync():
    logger.info("Generating daily compliance report")

    async def _generate():
        report_service = ReportService()
        (pdf_path, excel_path), stats = await report_service.generate_daily_report()

        notif_service = NotificationService()
        summary = {
            "total_data_collected": stats.total_data_collected,
            "total_events_detected": stats.total_events_detected,
            "total_tickets_created": stats.total_tickets_created,
            "total_tickets_closed": stats.total_tickets_closed,
            "overdue_tickets": stats.overdue_tickets,
            "critical_events": stats.events_by_severity.get("critical", 0),
            "completion_rate": stats.completion_rate,
            "on_time_rate": stats.on_time_rate,
        }
        report_date = stats.stat_date

        await notif_service.notify_daily_report(
            report_date=report_date,
            pdf_path=pdf_path,
            excel_path=excel_path,
            summary_stats=summary,
        )

        return {
            "pdf_path": pdf_path,
            "excel_path": excel_path,
            "summary": summary,
        }

    return _run_async(_generate())


def _send_critical_event_notifications_sync(event_ids: list):
    logger.info("Sending critical event notifications", count=len(event_ids))

    async def _notify():
        notif_service = NotificationService()
        from sqlalchemy import select
        from app.models.investigation import ComplianceEvent
        import uuid

        results = []
        async with get_db_context() as db:
            for eid in event_ids:
                ev_result = await db.execute(
                    select(ComplianceEvent).where(
                        ComplianceEvent.id == uuid.UUID(eid)
                    )
                )
                event = ev_result.scalar_one_or_none()
                if event:
                    success = await notif_service.notify_critical_event(event)
                    results.append({"event_code": event.event_code, "success": success})

        return {"notified": len(results), "results": results}

    return _run_async(_notify())


def _run_full_pipeline_sync(hours: int = 24):
    logger.info("Running full compliance pipeline", hours=hours)
    _collect_all_sources_sync(hours)
    detection_result = _run_violation_detection_sync(hours)
    _classify_and_create_tickets_sync()
    _process_escalations_sync()
    return detection_result


if CELERY_AVAILABLE:
    @celery_app.task(
        name="app.tasks.data_collection.collect_all_sources",
        queue="data_collection",
        max_retries=3,
        default_retry_delay=60,
    )
    def collect_all_sources(hours: int = 24):
        return _collect_all_sources_sync(hours)

    @celery_app.task(
        name="app.tasks.data_collection.collect_single_source",
        queue="data_collection",
    )
    def collect_single_source(source_type: str, hours: int = 24):
        return _collect_single_source_sync(source_type, hours)

    @celery_app.task(
        name="app.tasks.violation_detection.run_detection",
        queue="violation_detection",
    )
    def run_violation_detection(lookback_hours: int = 24):
        return _run_violation_detection_sync(lookback_hours)

    @celery_app.task(
        name="app.tasks.workflow.classify_and_create_tickets",
        queue="workflow_processing",
    )
    def classify_and_create_tickets():
        return _classify_and_create_tickets_sync()

    @celery_app.task(
        name="app.tasks.workflow.process_escalations",
        queue="workflow_processing",
    )
    def process_escalations():
        return _process_escalations_sync()

    @celery_app.task(
        name="app.tasks.workflow.collect_ticket_evidence",
        queue="workflow_processing",
    )
    def collect_ticket_evidence(ticket_id: str):
        return _collect_ticket_evidence_sync(ticket_id)

    @celery_app.task(
        name="app.tasks.reports.generate_daily_report",
        queue="reports",
    )
    def generate_daily_report():
        return _generate_daily_report_sync()

    @celery_app.task(
        name="app.tasks.notifications.send_critical_event_notifications",
        queue="notifications",
    )
    def send_critical_event_notifications(event_ids: list):
        return _send_critical_event_notifications_sync(event_ids)

    @celery_app.task(
        name="app.tasks.full_pipeline_run",
        queue="data_collection",
    )
    def run_full_pipeline(hours: int = 24):
        return _run_full_pipeline_sync(hours)

    CELERY_BEAT_SCHEDULE = {
        "collect-data-every-15-minutes": {
            "task": "app.tasks.data_collection.collect_all_sources",
            "schedule": timedelta(minutes=15),
            "args": [1],
        },
        "detect-violations-every-30-minutes": {
            "task": "app.tasks.violation_detection.run_detection",
            "schedule": timedelta(minutes=30),
            "args": [1],
        },
        "create-tickets-every-hour": {
            "task": "app.tasks.workflow.classify_and_create_tickets",
            "schedule": timedelta(hours=1),
        },
        "process-escalations-every-hour": {
            "task": "app.tasks.workflow.process_escalations",
            "schedule": timedelta(hours=1),
        },
        "generate-daily-report-at-3am": {
            "task": "app.tasks.reports.generate_daily_report",
            "schedule": timedelta(days=1),
            "options": {
                "eta": datetime.now().replace(
                    hour=3, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
            }
        },
    }
else:
    collect_all_sources = _make_sync_task(_collect_all_sources_sync)
    collect_single_source = _make_sync_task(_collect_single_source_sync)
    run_violation_detection = _make_sync_task(_run_violation_detection_sync)
    classify_and_create_tickets = _make_sync_task(_classify_and_create_tickets_sync)
    process_escalations = _make_sync_task(_process_escalations_sync)
    collect_ticket_evidence = _make_sync_task(_collect_ticket_evidence_sync)
    generate_daily_report = _make_sync_task(_generate_daily_report_sync)
    send_critical_event_notifications = _make_sync_task(_send_critical_event_notifications_sync)
    run_full_pipeline = _make_sync_task(_run_full_pipeline_sync)
    CELERY_BEAT_SCHEDULE = {}


__all__ = [
    "collect_all_sources",
    "collect_single_source",
    "run_violation_detection",
    "classify_and_create_tickets",
    "process_escalations",
    "collect_ticket_evidence",
    "generate_daily_report",
    "send_critical_event_notifications",
    "run_full_pipeline",
    "CELERY_AVAILABLE",
    "CELERY_BEAT_SCHEDULE",
]
