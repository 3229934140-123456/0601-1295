from __future__ import annotations
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, date
from uuid import UUID
import math
import os
from fastapi import APIRouter, Query, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import FileResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core import logger, settings
from app.core.constants import (
    SeverityLevel, EventType, TicketStatus,
    ViolationResult, DisciplinaryAction,
    InvestigationConclusion, LogActionType
)
from app.models.investigation import (
    InvestigationTicket, ComplianceEvent,
    EvidenceItem, EvidencePackage
)
from app.models.organization import Employee, InvestigationOfficer, Department
from app.models.compliance import (
    SystemLog, EmployeeReport, ComplianceProfile,
    EventTimeline
)
from app.schemas import (
    TicketBrief, TicketQueryParams, TicketListResponse,
    ComplianceEventBrief, ComplianceEventQueryParams,
    ComplianceEventListResponse,
    EmployeeReportSubmit, EmployeeReportResponse,
    EmployeeReportProcess,
    InvestigationConclusionSubmit, ApprovalDecision,
    EvidenceGenerateRequest,
    ExportRequest, ExportResponse,
    DashboardStats,
    LogQueryParams, LogResponse, LogListResponse,
    ProfileQueryParams, ComplianceProfileBrief,
    ProfileListResponse,
    PaginationParams, PaginationResponse,
    PipelineTriggerRequest, PipelineTriggerResponse,
    APIResponse,
)

from app.services import (
    EmployeeReportService,
    ReportService,
    QueryAndExportService,
)
from app.workflows import (
    InvestigationWorkflowService,
    EvidenceCollectionService,
)
from app.tasks import (
    collect_all_sources,
    run_violation_detection,
    classify_and_create_tickets,
    process_escalations,
    generate_daily_report,
    run_full_pipeline,
)

router = APIRouter(prefix="/api/v1", tags=["core"])


def _make_pagination(
    page: int, page_size: int, total: int
) -> PaginationResponse:
    return PaginationResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if page_size > 0 else 0,
    )


@router.get("/health", response_model=APIResponse)
async def health_check():
    return APIResponse(
        success=True,
        message="合规管理系统API运行正常",
        data={
            "version": "1.0.0",
            "timestamp": datetime.utcnow().isoformat(),
            "database": "connected",
        },
    )


@router.get("/tickets", response_model=TicketListResponse)
async def list_tickets(
    params: TicketQueryParams = Depends(),
    pagination: PaginationParams = Depends(),
    service: QueryAndExportService = Depends(lambda: QueryAndExportService()),
):
    tickets, total = await service.query_tickets(
        employee_id=params.employee_id,
        event_type=params.event_type,
        severity=params.severity,
        status=params.status,
        department_id=params.department_id,
        assigned_officer_id=params.assigned_officer_id,
        date_from=params.date_from,
        date_to=params.date_to,
        keyword=params.keyword,
        page=pagination.page,
        page_size=pagination.page_size,
        sort_by=params.sort_by,
        sort_order=params.sort_order,
    )
    return TicketListResponse(
        pagination=_make_pagination(pagination.page, pagination.page_size, total),
        items=[TicketBrief.model_validate(t) for t in tickets],
    )


@router.get("/tickets/{ticket_id}/lifecycle")
async def get_ticket_lifecycle(
    ticket_id: UUID,
    service: QueryAndExportService = Depends(lambda: QueryAndExportService()),
):
    lifecycle = await service.get_ticket_full_lifecycle(ticket_id)
    if not lifecycle:
        raise HTTPException(status_code=404, detail="工单不存在")
    return APIResponse(data=lifecycle)


@router.post("/tickets/export", response_model=ExportResponse)
async def export_tickets(
    request: ExportRequest,
    service: QueryAndExportService = Depends(lambda: QueryAndExportService()),
):
    filepath = await service.export_tickets(
        query_params=request.query_params,
        export_format=request.export_format,
        requester_id=request.requester_id,
    )
    filename = os.path.basename(filepath)

    _, total = await service.query_tickets(
        **request.query_params,
        page=1,
        page_size=1,
    )

    return ExportResponse(
        file_path=filepath,
        filename=filename,
        record_count=total,
        format=request.export_format,
    )


@router.get("/events", response_model=ComplianceEventListResponse)
async def list_compliance_events(
    params: ComplianceEventQueryParams = Depends(),
    pagination: PaginationParams = Depends(),
    service: QueryAndExportService = Depends(lambda: QueryAndExportService()),
):
    events, total = await service.query_compliance_events(
        employee_id=params.employee_id,
        event_type=params.event_type,
        severity=params.severity,
        department_id=params.department_id,
        date_from=params.date_from,
        date_to=params.date_to,
        has_ticket=params.has_ticket,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    brief_events = []
    for e in events:
        e.has_ticket = e.ticket_id is not None
        brief_events.append(ComplianceEventBrief.model_validate(e))
    return ComplianceEventListResponse(
        pagination=_make_pagination(pagination.page, pagination.page_size, total),
        items=brief_events,
    )


@router.post("/reports/submit", response_model=EmployeeReportResponse)
async def submit_employee_report(
    request: EmployeeReportSubmit,
    service: EmployeeReportService = Depends(lambda: EmployeeReportService()),
):
    try:
        event_type_enum = EventType(request.reported_event_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的事件类型: {request.reported_event_type}")

    severity_enum = None
    if request.reported_severity:
        try:
            severity_enum = SeverityLevel(request.reported_severity)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的严重程度: {request.reported_severity}")

    report = await service.submit_report(
        reporter_employee_id=request.reporter_employee_id,
        is_anonymous=request.is_anonymous,
        reported_employee_name=request.reported_employee_name,
        reported_event_type=event_type_enum,
        event_date=request.event_date,
        title=request.title,
        description=request.description,
        reported_severity=severity_enum,
        reporter_name=request.reporter_name,
        reporter_contact=request.reporter_contact,
        key_details=request.key_details,
        witness_names=request.witness_names,
        evidence_description=request.evidence_description,
        evidence_files=request.evidence_files,
        impact_assessment=request.impact_assessment,
        event_location=request.event_location,
        reported_employee_id=request.reported_employee_id,
    )

    response = EmployeeReportResponse(
        id=report.id,
        report_number=report.report_number,
        status=report.status,
        is_duplicate=report.is_duplicate,
        matched_with=(
            report.duplicate_of_report.report_number
            if report.duplicate_of_report else None
        ),
        created_at=report.created_at,
    )
    return response


@router.post("/reports/process", response_model=APIResponse)
async def process_employee_report(
    request: EmployeeReportProcess,
    service: EmployeeReportService = Depends(lambda: EmployeeReportService()),
):
    report = await service.process_report(
        report_id=request.report_id,
        reviewer_id=request.reviewer_id,
        action=request.action,
        review_notes=request.review_notes,
        target_ticket_id=request.target_ticket_id,
    )
    return APIResponse(
        message=f"申报单处理完成: {request.action}",
        data={
            "report_number": report.report_number,
            "status": report.status,
            "merged_ticket_id": str(report.merged_ticket_id) if report.merged_ticket_id else None,
        },
    )


@router.post("/investigations/start", response_model=APIResponse)
async def start_investigation(
    ticket_id: UUID,
    officer_id: UUID,
    notes: Optional[str] = None,
    service: InvestigationWorkflowService = Depends(lambda: InvestigationWorkflowService()),
):
    try:
        ticket = await service.start_investigation(ticket_id, officer_id, notes)
        return APIResponse(
            message="调查已启动",
            data={"ticket_number": ticket.ticket_number, "status": ticket.status},
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/investigations/conclusion", response_model=APIResponse)
async def submit_conclusion(
    request: InvestigationConclusionSubmit,
    service: InvestigationWorkflowService = Depends(lambda: InvestigationWorkflowService()),
):
    try:
        conclusion_enum = InvestigationConclusion(request.conclusion)
        result_enum = ViolationResult(request.violation_result)
        action_enum = None
        if request.disciplinary_action:
            action_enum = DisciplinaryAction(request.disciplinary_action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        ticket = await service.submit_conclusion(
            ticket_id=request.ticket_id,
            officer_id=request.officer_id,
            conclusion=conclusion_enum,
            conclusion_text=request.conclusion_text,
            violation_result=result_enum,
            disciplinary_action=action_enum,
            estimated_hours=request.estimated_hours,
        )
        return APIResponse(
            message="调查结论已提交，进入审批流程",
            data={
                "ticket_number": ticket.ticket_number,
                "status": ticket.status,
                "violation_result": ticket.violation_result,
            },
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/investigations/approval", response_model=APIResponse)
async def process_approval(
    request: ApprovalDecision,
    service: InvestigationWorkflowService = Depends(lambda: InvestigationWorkflowService()),
):
    try:
        ticket = await service.complete_investigation(
            ticket_id=request.ticket_id,
            approver_id=request.approver_id,
            approval_decision=request.decision,
            approval_comments=request.comments,
        )
        return APIResponse(
            message=f"审批已完成: {request.decision}",
            data={
                "ticket_number": ticket.ticket_number,
                "status": ticket.status,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/evidence/generate", response_model=APIResponse)
async def generate_evidence_package(
    request: EvidenceGenerateRequest,
    background_tasks: BackgroundTasks,
    service: EvidenceCollectionService = Depends(lambda: EvidenceCollectionService()),
):
    background_tasks.add_task(
        run_full_evidence_collection, request.ticket_id
    )

    return APIResponse(
        message="证据包正在后台生成，请稍后查看",
        data={"ticket_id": str(request.ticket_id)},
    )


async def run_full_evidence_collection(ticket_id: UUID):
    try:
        evidence_service = EvidenceCollectionService()
        await evidence_service.collect_evidence_for_ticket(ticket_id)
    except Exception as e:
        logger.error("Evidence package generation failed", ticket_id=str(ticket_id), error=str(e))


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    service: QueryAndExportService = Depends(lambda: QueryAndExportService()),
):
    if not date_from:
        date_from = datetime.utcnow() - timedelta(days=30)
    if not date_to:
        date_to = datetime.utcnow()

    stats = await service.get_statistics_dashboard(date_from, date_to)
    return DashboardStats(
        date_from=date_from,
        date_to=date_to,
        **stats,
    )


@router.get("/profiles", response_model=ProfileListResponse)
async def list_compliance_profiles(
    params: ProfileQueryParams = Depends(),
    pagination: PaginationParams = Depends(),
    service: QueryAndExportService = Depends(lambda: QueryAndExportService()),
):
    profiles, total = await service.query_compliance_profiles(
        employee_name=params.employee_name,
        department_id=params.department_id,
        risk_level=params.risk_level,
        min_score=params.min_score,
        max_score=params.max_score,
        has_violations=params.has_violations,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return ProfileListResponse(
        pagination=_make_pagination(pagination.page, pagination.page_size, total),
        items=[ComplianceProfileBrief.model_validate(p) for p in profiles],
    )


@router.get("/logs", response_model=LogListResponse)
async def list_operation_logs(
    params: LogQueryParams = Depends(),
    pagination: PaginationParams = Depends(),
    service: QueryAndExportService = Depends(lambda: QueryAndExportService()),
):
    logs, total = await service.query_operation_logs(
        user_id=params.user_id,
        action_type=params.action_type,
        target_type=params.target_type,
        date_from=params.date_from,
        date_to=params.date_to,
        log_level=params.log_level,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return LogListResponse(
        pagination=_make_pagination(pagination.page, pagination.page_size, total),
        items=[LogResponse.model_validate(l) for l in logs],
    )


@router.post("/logs/export", response_model=ExportResponse)
async def export_operation_logs(
    requester_id: UUID,
    params: LogQueryParams = Depends(),
    service: QueryAndExportService = Depends(lambda: QueryAndExportService()),
):
    query_params = {
        "user_id": params.user_id,
        "action_type": params.action_type,
        "target_type": params.target_type,
        "date_from": params.date_from,
        "date_to": params.date_to,
        "log_level": params.log_level,
    }
    filepath = await service.export_operation_logs(
        query_params=query_params,
        requester_id=requester_id,
    )
    _, total = await service.query_operation_logs(
        **query_params,
        page=1,
        page_size=1,
    )
    return ExportResponse(
        file_path=filepath,
        filename=os.path.basename(filepath),
        record_count=total,
        format="csv",
    )


@router.post("/pipeline/trigger", response_model=PipelineTriggerResponse)
async def trigger_pipeline(
    request: PipelineTriggerRequest,
):
    task = run_full_pipeline.delay(hours=request.lookback_hours)
    return PipelineTriggerResponse(
        task_id=str(task.id),
        task_type="full_pipeline",
        triggered_at=datetime.utcnow(),
        lookback_hours=request.lookback_hours,
    )


@router.post("/tasks/collect-data", response_model=APIResponse)
async def trigger_data_collection(hours: int = 24):
    task = collect_all_sources.delay(hours=hours)
    return APIResponse(
        message="数据采集任务已启动",
        data={"task_id": str(task.id), "lookback_hours": hours},
    )


@router.post("/tasks/detect-violations", response_model=APIResponse)
async def trigger_violation_detection(lookback_hours: int = 24):
    task = run_violation_detection.delay(lookback_hours=lookback_hours)
    return APIResponse(
        message="违规检测任务已启动",
        data={"task_id": str(task.id)},
    )


@router.post("/tasks/generate-report", response_model=APIResponse)
async def trigger_report_generation():
    task = generate_daily_report.delay()
    return APIResponse(
        message="每日报告生成任务已启动",
        data={"task_id": str(task.id)},
    )


@router.get("/reports/daily/download")
async def download_daily_report(
    date: date = Query(..., description="报告日期 (YYYY-MM-DD)"),
    format: str = Query("pdf", pattern="^(pdf|xlsx)$"),
):
    date_str = date.strftime("%Y%m%d")
    filename = f"合规日报_{date_str}.{format}"
    filepath = os.path.join(settings.REPORT_OUTPUT_DIR, filename)

    if not os.path.exists(filepath):
        raise HTTPException(
            status_code=404,
            detail=f"指定日期的报告不存在: {filename}. 请先生成报告。"
        )

    return FileResponse(
        path=filepath,
        filename=filename,
        media_type=(
            "application/pdf" if format == "pdf"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


@router.get("/exports/download/{filename}")
async def download_export_file(filename: str):
    filepath = os.path.join(settings.REPORT_OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="导出文件不存在")

    ext = os.path.splitext(filename)[1].lower()
    media_type = "text/csv" if ext == ".csv" else (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if ext == ".xlsx" else "application/octet-stream"
    )

    return FileResponse(
        path=filepath,
        filename=filename,
        media_type=media_type,
    )


@router.get("/options/constants")
async def get_system_constants():
    return APIResponse(
        data={
            "severity_levels": [
                {"value": "general", "label": "一般", "deadline_hours": 7 * 24},
                {"value": "important", "label": "重要", "deadline_hours": 3 * 24},
                {"value": "critical", "label": "重大", "deadline_hours": 48},
            ],
            "event_types": [
                {"value": et.value, "label": label}
                for et, label in [
                    (EventType.DATA_LEAK, "数据泄露"),
                    (EventType.UNAUTHORIZED_ACCESS, "未授权访问"),
                    (EventType.FRAUD, "财务欺诈"),
                    (EventType.CONFLICT_OF_INTEREST, "利益冲突"),
                    (EventType.HARASSMENT, "职场骚扰"),
                    (EventType.DISCRIMINATION, "歧视行为"),
                    (EventType.THEFT, "盗窃行为"),
                    (EventType.POLICY_VIOLATION, "违反制度"),
                    (EventType.SUSPICIOUS_COMMUNICATION, "可疑通讯"),
                    (EventType.ABNORMAL_BEHAVIOR, "异常行为"),
                    (EventType.OTHER, "其他"),
                ]
            ],
            "ticket_statuses": [
                {"value": "pending", "label": "待分配"},
                {"value": "assigned", "label": "已分配"},
                {"value": "under_investigation", "label": "调查中"},
                {"value": "evidence_collected", "label": "已取证"},
                {"value": "conclusion_submitted", "label": "待审批"},
                {"value": "under_approval", "label": "审批中"},
                {"value": "approved", "label": "审批通过"},
                {"value": "rejected", "label": "审批驳回"},
                {"value": "closed", "label": "已关闭"},
                {"value": "escalated", "label": "已升级"},
            ],
            "violation_results": [
                {"value": "confirmed", "label": "确认违规"},
                {"value": "unconfirmed", "label": "无法确认"},
                {"value": "false_positive", "label": "误报排除"},
            ],
            "disciplinary_actions": [
                {"value": "warning", "label": "警告"},
                {"value": "serious_warning", "label": "记过"},
                {"value": "demotion", "label": "降级"},
                {"value": "salary_reduction", "label": "降薪"},
                {"value": "permission_freeze", "label": "冻结权限"},
                {"value": "termination", "label": "解除合同"},
                {"value": "training", "label": "合规培训"},
                {"value": "no_action", "label": "免于处罚"},
            ],
            "risk_levels": [
                {"value": "low", "label": "低风险"},
                {"value": "medium", "label": "中风险"},
                {"value": "high", "label": "高风险"},
            ],
        }
    )
