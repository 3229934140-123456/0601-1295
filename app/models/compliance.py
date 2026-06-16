from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean,
    Text, ForeignKey, Index, JSON, Float, ARRAY
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.core.database import Base


class ApprovalRecord(Base):
    __tablename__ = "approval_records"
    __table_args__ = (
        Index("idx_approval_ticket", "ticket_id", "approval_order"),
        Index("idx_approval_approver", "approver_id", "status"),
        Index("idx_approval_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=False, index=True)
    approval_type = Column(String(50), nullable=False)
    approval_order = Column(Integer, default=1)
    total_levels = Column(Integer, default=3)
    current_level = Column(Integer, default=1)
    approver_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=False)
    approver_name = Column(String(100), nullable=True)
    approver_title = Column(String(100), nullable=True)
    status = Column(String(20), default="pending", index=True)
    decision = Column(String(20), nullable=True)
    comments = Column(Text, nullable=True)
    attachments = Column(JSON, default=list, nullable=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)
    deadline = Column(DateTime, nullable=True)
    escalated_to_id = Column(UUID(as_uuid=True), nullable=True)
    escalated_at = Column(DateTime, nullable=True)
    escalation_reason = Column(Text, nullable=True)
    approver_ip = Column(String(50), nullable=True)
    approval_signature = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    ticket = relationship("InvestigationTicket", back_populates="approvals")
    approver = relationship("Employee", foreign_keys=[approver_id])


class ComplianceProfile(Base):
    __tablename__ = "compliance_profiles"
    __table_args__ = (
        Index("idx_profile_employee", "employee_id", unique=True),
        Index("idx_profile_risk", "risk_level"),
        Index("idx_profile_score", "compliance_score"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=False, unique=True, index=True)
    employee_name = Column(String(100), nullable=True)
    department_id = Column(UUID(as_uuid=True), nullable=True)
    department_name = Column(String(200), nullable=True)
    compliance_score = Column(Integer, default=100, index=True)
    risk_level = Column(String(20), default="low", index=True)
    total_events_count = Column(Integer, default=0)
    confirmed_violations_count = Column(Integer, default=0)
    false_positives_count = Column(Integer, default=0)
    pending_investigations_count = Column(Integer, default=0)
    warnings_count = Column(Integer, default=0)
    disciplinary_actions = Column(JSON, default=list, nullable=True)
    last_violation_date = Column(DateTime, nullable=True)
    last_training_date = Column(DateTime, nullable=True)
    next_training_due = Column(DateTime, nullable=True)
    restrictions = Column(JSON, default=list, nullable=True)
    restrictions_expiry = Column(JSON, default=dict, nullable=True)
    active_tickets = Column(ARRAY(UUID(as_uuid=True)), default=list, nullable=True)
    closed_tickets_count = Column(Integer, default=0)
    total_investigation_hours = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("Employee", back_populates="compliance_profile")
    history_entries = relationship("ComplianceProfileHistory", back_populates="profile")


class ComplianceProfileHistory(Base):
    __tablename__ = "compliance_profile_history"
    __table_args__ = (
        Index("idx_profile_hist_profile", "profile_id", "changed_at"),
        Index("idx_profile_hist_ticket", "related_ticket_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("compliance_profiles.id"), nullable=False, index=True)
    changed_by_id = Column(UUID(as_uuid=True), nullable=True)
    changed_by_name = Column(String(100), nullable=True)
    change_type = Column(String(50), nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow, index=True)
    field_changed = Column(String(100), nullable=True)
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    related_ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=True, index=True)
    related_event_id = Column(UUID(as_uuid=True), nullable=True)
    reason = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    profile = relationship("ComplianceProfile", back_populates="history_entries")


class DisciplinaryActionRecord(Base):
    __tablename__ = "disciplinary_action_records"
    __table_args__ = (
        Index("idx_disciplinary_employee", "employee_id", "action_date"),
        Index("idx_disciplinary_ticket", "ticket_id", unique=True),
        Index("idx_disciplinary_type", "action_type"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=False, unique=True, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=False, index=True)
    employee_name = Column(String(100), nullable=True)
    department_id = Column(UUID(as_uuid=True), nullable=True)
    department_name = Column(String(200), nullable=True)
    action_type = Column(String(50), nullable=False)
    action_level = Column(String(20), nullable=True)
    severity_level = Column(String(20), nullable=True)
    action_date = Column(DateTime, default=datetime.utcnow, index=True)
    effective_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    description = Column(Text, nullable=True)
    event_summary = Column(Text, nullable=True)
    action_details = Column(JSON, default=dict, nullable=True)
    related_event_ids = Column(ARRAY(UUID(as_uuid=True)), default=list, nullable=True)
    is_appealable = Column(Boolean, default=True)
    appeal_deadline = Column(DateTime, nullable=True)
    appealed = Column(Boolean, default=False)
    appeal_result = Column(String(50), nullable=True)
    approval_id = Column(UUID(as_uuid=True), nullable=True)
    issued_by_id = Column(UUID(as_uuid=True), nullable=True)
    issued_by_name = Column(String(100), nullable=True)
    executed_by_id = Column(UUID(as_uuid=True), nullable=True)
    executed_at = Column(DateTime, nullable=True)
    hr_acknowledged = Column(Boolean, default=False)
    hr_acknowledged_at = Column(DateTime, nullable=True)
    employee_acknowledged = Column(Boolean, default=False)
    employee_acknowledged_at = Column(DateTime, nullable=True)
    status = Column(String(30), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("Employee", foreign_keys=[employee_id])
    ticket = relationship("InvestigationTicket")


class EmployeeReport(Base):
    __tablename__ = "employee_reports"
    __table_args__ = (
        Index("idx_report_reporter", "reporter_employee_id", "created_at"),
        Index("idx_report_type", "reported_event_type"),
        Index("idx_report_status", "status"),
        Index("idx_report_dedupe", "deduplication_hash"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_number = Column(String(30), nullable=False, unique=True)
    reporter_employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    reporter_name = Column(String(100), nullable=True)
    reporter_contact = Column(String(200), nullable=True)
    is_anonymous = Column(Boolean, default=False)
    reported_employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    reported_employee_name = Column(String(100), nullable=True)
    reported_department_id = Column(UUID(as_uuid=True), nullable=True)
    reported_department_name = Column(String(200), nullable=True)
    reported_event_type = Column(String(50), nullable=False)
    reported_severity = Column(String(20), nullable=True)
    event_date = Column(DateTime, nullable=True)
    event_location = Column(String(200), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=False)
    key_details = Column(JSON, default=list, nullable=True)
    witness_names = Column(JSON, default=list, nullable=True)
    evidence_description = Column(Text, nullable=True)
    evidence_files = Column(JSON, default=list, nullable=True)
    impact_assessment = Column(Text, nullable=True)
    source = Column(String(50), default="web_portal")
    status = Column(String(30), default="submitted", index=True)
    merged_ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=True, index=True)
    is_duplicate = Column(Boolean, default=False)
    duplicate_of_report_id = Column(UUID(as_uuid=True), ForeignKey("employee_reports.id"), nullable=True)
    deduplication_hash = Column(String(64), nullable=True, index=True)
    matching_event_ids = Column(ARRAY(UUID(as_uuid=True)), default=list, nullable=True)
    reviewed_by_id = Column(UUID(as_uuid=True), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(Text, nullable=True)
    priority = Column(Integer, default=0)
    tags = Column(ARRAY(String(100)), default=list, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    reporter = relationship("Employee", foreign_keys=[reporter_employee_id])
    reported_employee = relationship("Employee", foreign_keys=[reported_employee_id])
    merged_ticket = relationship("InvestigationTicket", back_populates="employee_reports")
    duplicates = relationship("EmployeeReport", remote_side=[id])


class EventTimeline(Base):
    __tablename__ = "event_timelines"
    __table_args__ = (
        Index("idx_timeline_event", "event_id", "timestamp"),
        Index("idx_timeline_ticket", "ticket_id", "timestamp"),
        Index("idx_timeline_employee", "employee_id", "timestamp"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("compliance_events.id"), nullable=True, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=True, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True, index=True)
    employee_name = Column(String(100), nullable=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    timeline_type = Column(String(50), nullable=False)
    data_source = Column(String(50), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    source_record_id = Column(UUID(as_uuid=True), nullable=True)
    source_table = Column(String(100), nullable=True)
    location = Column(String(200), nullable=True)
    related_entities = Column(JSON, default=list, nullable=True)
    metadata = Column(JSON, default=dict, nullable=True)
    evidence_link = Column(String(500), nullable=True)
    is_key_event = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("ComplianceEvent", back_populates="timeline_entries")
    ticket = relationship("InvestigationTicket", back_populates="timeline_entries")
    employee = relationship("Employee", foreign_keys=[employee_id])


class SystemLog(Base):
    __tablename__ = "system_logs"
    __table_args__ = (
        Index("idx_syslog_time_action", "created_at", "action_type"),
        Index("idx_syslog_user", "user_id", "created_at"),
        Index("idx_syslog_target", "target_type", "target_id"),
        Index("idx_syslog_level", "log_level"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    log_level = Column(String(20), default="INFO", index=True)
    action_type = Column(String(50), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    user_name = Column(String(100), nullable=True)
    user_role = Column(String(50), nullable=True)
    target_type = Column(String(50), nullable=True)
    target_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    target_name = Column(String(200), nullable=True)
    action_details = Column(JSON, default=dict, nullable=True)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    session_id = Column(String(100), nullable=True)
    request_id = Column(String(100), nullable=True)
    status = Column(String(20), default="success")
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    old_values = Column(JSON, nullable=True)
    new_values = Column(JSON, nullable=True)
    changes_summary = Column(JSON, default=list, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __mapper_args__ = {
        "eager_defaults": True,
    }


class DailyStatistics(Base):
    __tablename__ = "daily_statistics"
    __table_args__ = (
        Index("idx_daily_stats_date", "stat_date", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stat_date = Column(DateTime, nullable=False, unique=True, index=True)
    total_data_collected = Column(Integer, default=0)
    data_by_source = Column(JSON, default=dict, nullable=True)
    total_events_detected = Column(Integer, default=0)
    events_by_type = Column(JSON, default=dict, nullable=True)
    events_by_severity = Column(JSON, default=dict, nullable=True)
    duplicate_events = Column(Integer, default=0)
    total_tickets_created = Column(Integer, default=0)
    tickets_created_by_severity = Column(JSON, default=dict, nullable=True)
    total_tickets_closed = Column(Integer, default=0)
    tickets_closed_by_result = Column(JSON, default=dict, nullable=True)
    tickets_closed_by_type = Column(JSON, default=dict, nullable=True)
    pending_tickets = Column(Integer, default=0)
    overdue_tickets = Column(Integer, default=0)
    escalated_tickets = Column(Integer, default=0)
    tickets_in_approval = Column(Integer, default=0)
    employee_reports_submitted = Column(Integer, default=0)
    employee_reports_merged = Column(Integer, default=0)
    confirmed_violations = Column(Integer, default=0)
    false_positives = Column(Integer, default=0)
    disciplinary_actions_issued = Column(JSON, default=dict, nullable=True)
    avg_processing_hours = Column(JSON, default=dict, nullable=True)
    avg_processing_hours_by_severity = Column(JSON, default=dict, nullable=True)
    avg_processing_hours_by_type = Column(JSON, default=dict, nullable=True)
    completion_rate = Column(Float, default=0.0)
    on_time_rate = Column(Float, default=0.0)
    officer_workload = Column(JSON, default=list, nullable=True)
    department_statistics = Column(JSON, default=list, nullable=True)
    top_event_types = Column(JSON, default=list, nullable=True)
    trend_7days = Column(JSON, default=dict, nullable=True)
    trend_30days = Column(JSON, default=dict, nullable=True)
    reports_generated = Column(Integer, default=0)
    notifications_sent = Column(Integer, default=0)
    anomalies_detected = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
