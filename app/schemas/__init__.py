from __future__ import annotations
from typing import List, Optional, Dict, Any
from datetime import datetime, date
from uuid import UUID
from pydantic import BaseModel, Field, EmailStr, field_validator
from app.core.constants import (
    SeverityLevel, EventType, TicketStatus,
    ViolationResult, DisciplinaryAction,
    InvestigationConclusion, DataSourceType
)


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)


class PaginationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int


class EmployeeBase(BaseModel):
    employee_id: str
    name: str
    email: EmailStr
    department_id: Optional[UUID] = None
    position: Optional[str] = None
    job_level: Optional[str] = None


class EmployeeResponse(EmployeeBase):
    id: UUID
    department_name: Optional[str] = None
    employment_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ComplianceEventBrief(BaseModel):
    id: UUID
    event_code: str
    event_type: str
    severity: str
    detected_at: datetime
    title: str
    risk_score: int
    confidence: float
    subject_employee_name: Optional[str] = None
    has_ticket: bool

    class Config:
        from_attributes = True


class TicketBrief(BaseModel):
    id: UUID
    ticket_number: str
    title: str
    event_type: str
    severity: str
    status: str
    subject_employee_name: Optional[str] = None
    department_name: Optional[str] = None
    assigned_officer_name: Optional[str] = None
    created_at: datetime
    deadline: Optional[datetime] = None
    is_overdue: bool
    priority_score: int

    class Config:
        from_attributes = True


class TicketQueryParams(BaseModel):
    employee_id: Optional[UUID] = None
    event_type: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    department_id: Optional[UUID] = None
    assigned_officer_id: Optional[UUID] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    keyword: Optional[str] = None
    sort_by: str = "created_at"
    sort_order: str = "desc"


class TicketListResponse(BaseModel):
    pagination: PaginationResponse
    items: List[TicketBrief]


class ComplianceEventQueryParams(BaseModel):
    employee_id: Optional[UUID] = None
    event_type: Optional[str] = None
    severity: Optional[str] = None
    department_id: Optional[UUID] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    has_ticket: Optional[bool] = None


class ComplianceEventListResponse(BaseModel):
    pagination: PaginationResponse
    items: List[ComplianceEventBrief]


class EmployeeReportSubmit(BaseModel):
    is_anonymous: bool = False
    reporter_employee_id: Optional[UUID] = None
    reporter_name: Optional[str] = None
    reporter_contact: Optional[str] = None
    reported_employee_name: str = Field(..., min_length=1)
    reported_employee_id: Optional[UUID] = None
    reported_event_type: str
    reported_severity: Optional[str] = None
    event_date: Optional[datetime] = None
    event_location: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=10)
    key_details: Optional[List[str]] = Field(default_factory=list)
    witness_names: Optional[List[str]] = Field(default_factory=list)
    evidence_description: Optional[str] = None
    evidence_files: Optional[List[str]] = Field(default_factory=list)
    impact_assessment: Optional[str] = None


class EmployeeReportResponse(BaseModel):
    id: UUID
    report_number: str
    status: str
    is_duplicate: bool
    matched_with: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class EmployeeReportProcess(BaseModel):
    report_id: UUID
    reviewer_id: UUID
    action: str = Field(pattern="^(merge|dismiss|flag_for_review)$")
    review_notes: Optional[str] = None
    target_ticket_id: Optional[UUID] = None


class InvestigationConclusionSubmit(BaseModel):
    ticket_id: UUID
    officer_id: UUID
    conclusion: str
    conclusion_text: str = Field(..., min_length=20)
    violation_result: str
    disciplinary_action: Optional[str] = None
    estimated_hours: float = 0.0


class ApprovalDecision(BaseModel):
    ticket_id: UUID
    approver_id: UUID
    decision: str = Field(pattern="^(approved|rejected)$")
    comments: Optional[str] = None


class EvidenceGenerateRequest(BaseModel):
    ticket_id: UUID


class ExportRequest(BaseModel):
    query_params: Dict[str, Any] = Field(default_factory=dict)
    export_format: str = Field(default="xlsx", pattern="^(xlsx|csv)$")
    requester_id: Optional[UUID] = None


class ExportResponse(BaseModel):
    file_path: str
    filename: str
    record_count: int
    format: str


class DashboardStats(BaseModel):
    date_from: datetime
    date_to: datetime
    ticket_overview: Dict[str, Any]
    tickets_by_severity: Dict[str, int]
    tickets_by_type: Dict[str, int]
    top_departments: List[Dict[str, Any]]
    result_distribution: Dict[str, int]
    overdue_count: int
    pending_approvals: int
    avg_processing_hours: float
    officer_workload: List[Dict[str, Any]]


class LogQueryParams(BaseModel):
    user_id: Optional[UUID] = None
    action_type: Optional[str] = None
    target_type: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    log_level: Optional[str] = None


class LogResponse(BaseModel):
    id: UUID
    created_at: datetime
    log_level: str
    action_type: str
    user_name: Optional[str] = None
    target_type: Optional[str] = None
    target_name: Optional[str] = None
    status: str
    action_details: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class LogListResponse(BaseModel):
    pagination: PaginationResponse
    items: List[LogResponse]


class ProfileQueryParams(BaseModel):
    employee_name: Optional[str] = None
    department_id: Optional[UUID] = None
    risk_level: Optional[str] = None
    min_score: Optional[int] = None
    max_score: Optional[int] = None
    has_violations: Optional[bool] = None


class ComplianceProfileBrief(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: str
    department_name: Optional[str] = None
    compliance_score: int
    risk_level: str
    total_events_count: int
    confirmed_violations_count: int
    last_violation_date: Optional[datetime] = None
    updated_at: datetime

    class Config:
        from_attributes = True


class ProfileListResponse(BaseModel):
    pagination: PaginationResponse
    items: List[ComplianceProfileBrief]


class PipelineTriggerRequest(BaseModel):
    lookback_hours: int = Field(default=24, ge=1, le=720)


class PipelineTriggerResponse(BaseModel):
    task_id: str
    task_type: str
    triggered_at: datetime
    lookback_hours: int


class DepartmentSeedData(BaseModel):
    code: str
    name: str
    description: Optional[str] = None


class EmployeeSeedData(BaseModel):
    employee_id: str
    name: str
    email: EmailStr
    department_code: str
    position: Optional[str] = None
    job_level: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None


class OfficerSeedData(BaseModel):
    employee_id: str
    specializations: List[str] = Field(default_factory=list)
    max_ticket_capacity: int = 10


class APIResponse(BaseModel):
    success: bool = True
    message: str = "操作成功"
    data: Optional[Any] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
