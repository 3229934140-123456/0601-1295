from datetime import datetime, timedelta
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean,
    Text, ForeignKey, Index, JSON, Float, ARRAY
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.core.database import Base
from app.core.constants import (
    SeverityLevel, EventType, TicketStatus,
    ViolationResult, DisciplinaryAction, SEVERITY_TIME_LIMIT
)


class ComplianceRule(Base):
    __tablename__ = "compliance_rules"
    __table_args__ = (
        Index("idx_rule_active", "is_active"),
        Index("idx_rule_event_type", "event_type"),
        Index("idx_rule_severity", "severity"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_code = Column(String(100), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    event_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    data_sources = Column(ARRAY(String(50)), default=list, nullable=True)
    conditions = Column(JSON, nullable=False)
    threshold_config = Column(JSON, default=dict, nullable=True)
    score_weight = Column(Integer, default=10)
    is_active = Column(Boolean, default=True, index=True)
    version = Column(String(20), default="1.0")
    created_by = Column(UUID(as_uuid=True), nullable=True)
    effective_from = Column(DateTime, default=datetime.utcnow)
    effective_to = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    events = relationship("ComplianceEvent", back_populates="matched_rule")


class ComplianceEvent(Base):
    __tablename__ = "compliance_events"
    __table_args__ = (
        Index("idx_event_employee_time", "subject_employee_id", "detected_at"),
        Index("idx_event_severity_status", "severity", "status"),
        Index("idx_event_type_time", "event_type", "detected_at"),
        Index("idx_event_ticket", "ticket_id"),
        Index("idx_event_dedupe", "deduplication_hash", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_code = Column(String(50), nullable=False, unique=True)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("compliance_rules.id"), nullable=True)
    event_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    subject_employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True, index=True)
    subject_employee_name = Column(String(100), nullable=True)
    subject_department_id = Column(UUID(as_uuid=True), nullable=True)
    subject_department_name = Column(String(200), nullable=True)
    related_employee_ids = Column(ARRAY(UUID(as_uuid=True)), default=list, nullable=True)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    event_time = Column(DateTime, nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    evidence_summary = Column(Text, nullable=True)
    risk_score = Column(Integer, default=0)
    confidence = Column(Float, default=0.0)
    matched_data_sources = Column(ARRAY(String(50)), default=list, nullable=True)
    raw_data_record_ids = Column(ARRAY(UUID(as_uuid=True)), default=list, nullable=True)
    raw_data_refs = Column(JSON, default=list, nullable=True)
    status = Column(String(30), default="pending")
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=True, index=True)
    is_duplicate = Column(Boolean, default=False)
    duplicate_of_event_id = Column(UUID(as_uuid=True), ForeignKey("compliance_events.id"), nullable=True)
    deduplication_hash = Column(String(64), nullable=False, unique=True, index=True)
    detection_version = Column(String(20), default="1.0")
    extra_metadata = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    matched_rule = relationship("ComplianceRule", back_populates="events")
    subject_employee = relationship("Employee", foreign_keys=[subject_employee_id])
    ticket = relationship("InvestigationTicket", foreign_keys=[ticket_id], back_populates="events")
    duplicates = relationship("ComplianceEvent", remote_side=[id], backref="original_event")
    evidence_items = relationship("EvidenceItem", back_populates="event")
    timeline_entries = relationship("EventTimeline", back_populates="event")


class InvestigationTicket(Base):
    __tablename__ = "investigation_tickets"
    __table_args__ = (
        Index("idx_ticket_status_priority", "status", "severity"),
        Index("idx_ticket_officer_status", "assigned_officer_id", "status"),
        Index("idx_ticket_deadline", "deadline"),
        Index("idx_ticket_department", "department_id"),
        Index("idx_ticket_created", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_number = Column(String(30), nullable=False, unique=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    event_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="pending", index=True)
    subject_employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True, index=True)
    subject_employee_name = Column(String(100), nullable=True)
    department_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    department_name = Column(String(200), nullable=True)
    assigned_officer_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True, index=True)
    assigned_officer_name = Column(String(100), nullable=True)
    assigned_at = Column(DateTime, nullable=True)
    deadline = Column(DateTime, nullable=True)
    escalated_at = Column(DateTime, nullable=True)
    escalated_to_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    escalated_to_name = Column(String(100), nullable=True)
    escalation_count = Column(Integer, default=0)
    last_reminder_at = Column(DateTime, nullable=True)
    reminder_count = Column(Integer, default=0)
    violation_result = Column(String(30), default="pending")
    conclusion_text = Column(Text, nullable=True)
    conclusion_summary = Column(Text, nullable=True)
    disciplinary_action = Column(String(50), nullable=True)
    investigation_notes = Column(Text, nullable=True)
    estimated_hours = Column(Float, default=0.0)
    actual_hours = Column(Float, default=0.0)
    priority_score = Column(Integer, default=0)
    is_overdue = Column(Boolean, default=False)
    tags = Column(ARRAY(String(100)), default=list, nullable=True)
    parent_ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=True)
    created_by_id = Column(UUID(as_uuid=True), nullable=True)
    closed_at = Column(DateTime, nullable=True)
    closed_by_id = Column(UUID(as_uuid=True), nullable=True)
    closed_reason = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subject_employee = relationship("Employee", foreign_keys=[subject_employee_id], back_populates="tickets_as_subject")
    assigned_officer = relationship("Employee", foreign_keys=[assigned_officer_id], back_populates="tickets_as_officer")
    escalated_to = relationship("Employee", foreign_keys=[escalated_to_id])
    parent_ticket = relationship("InvestigationTicket", remote_side=[id])
    events = relationship("ComplianceEvent", back_populates="ticket")
    evidence_items = relationship("EvidenceItem", back_populates="ticket")
    evidence_package = relationship("EvidencePackage", uselist=False, back_populates="ticket")
    approvals = relationship("ApprovalRecord", back_populates="ticket")
    timeline_entries = relationship("EventTimeline", back_populates="ticket")
    employee_reports = relationship("EmployeeReport", back_populates="merged_ticket")

    @validates("severity")
    def set_deadline(self, key, value):
        if value in SEVERITY_TIME_LIMIT:
            if not self.deadline:
                self.deadline = datetime.utcnow() + SEVERITY_TIME_LIMIT[value]
        return value


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        Index("idx_evidence_ticket", "ticket_id"),
        Index("idx_evidence_event", "event_id"),
        Index("idx_evidence_type", "evidence_type"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=True, index=True)
    event_id = Column(UUID(as_uuid=True), ForeignKey("compliance_events.id"), nullable=True, index=True)
    evidence_type = Column(String(50), nullable=False, index=True)
    data_source = Column(String(50), nullable=True)
    source_record_id = Column(UUID(as_uuid=True), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    content_summary = Column(Text, nullable=True)
    content_path = Column(String(500), nullable=True)
    file_path = Column(String(500), nullable=True)
    file_name = Column(String(200), nullable=True)
    file_size = Column(Integer, nullable=True)
    file_hash = Column(String(64), nullable=True)
    related_employee_ids = Column(ARRAY(UUID(as_uuid=True)), default=list, nullable=True)
    event_timestamp = Column(DateTime, nullable=True)
    is_key_evidence = Column(Boolean, default=False)
    relevance_score = Column(Integer, default=0)
    chain_of_custody = Column(JSON, default=list, nullable=True)
    hash_sha256 = Column(String(64), nullable=True)
    collected_by = Column(UUID(as_uuid=True), nullable=True)
    collected_at = Column(DateTime, default=datetime.utcnow)
    verified_at = Column(DateTime, nullable=True)
    verified_by = Column(UUID(as_uuid=True), nullable=True)
    notes = Column(Text, nullable=True)
    extra_metadata = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("InvestigationTicket", back_populates="evidence_items")
    event = relationship("ComplianceEvent", back_populates="evidence_items")


class EvidencePackage(Base):
    __tablename__ = "evidence_packages"
    __table_args__ = (
        Index("idx_evidence_pkg_ticket", "ticket_id", unique=True),
        Index("idx_evidence_pkg_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("investigation_tickets.id"), nullable=False, unique=True, index=True)
    package_code = Column(String(50), nullable=False, unique=True)
    status = Column(String(20), default="generating")
    file_path = Column(String(500), nullable=True)
    file_name = Column(String(200), nullable=True)
    file_size = Column(Integer, default=0)
    file_hash = Column(String(64), nullable=True)
    evidence_count = Column(Integer, default=0)
    timeline_file_path = Column(String(500), nullable=True)
    financial_summary_path = Column(String(500), nullable=True)
    generated_by = Column(UUID(as_uuid=True), nullable=True)
    generated_at = Column(DateTime, nullable=True)
    generation_started_at = Column(DateTime, default=datetime.utcnow)
    generation_errors = Column(JSON, default=list, nullable=True)
    manifest = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    ticket = relationship("InvestigationTicket", back_populates="evidence_package")
