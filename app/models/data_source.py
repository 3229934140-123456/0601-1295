from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean,
    Text, ForeignKey, Index, JSON, BigInteger
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.core.database import Base
from app.core.constants import DataSourceType


class RawDataRecord(Base):
    __tablename__ = "raw_data_records"
    __table_args__ = (
        Index("idx_rawdata_source_time", "data_source", "recorded_at"),
        Index("idx_rawdata_employee_time", "employee_id", "recorded_at"),
        Index("idx_rawdata_processed", "is_processed"),
        Index("idx_rawdata_external_id", "data_source", "external_id", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_source = Column(String(50), nullable=False, index=True)
    external_id = Column(String(200), nullable=False)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True, index=True)
    employee_identifier = Column(String(200), nullable=True)
    recorded_at = Column(DateTime, nullable=False, index=True)
    collected_at = Column(DateTime, default=datetime.utcnow, index=True)
    raw_content = Column(JSON, nullable=False)
    summary = Column(Text, nullable=True)
    tags = Column(JSON, default=list, nullable=True)
    is_processed = Column(Boolean, default=False, index=True)
    is_flagged = Column(Boolean, default=False, index=True)
    processing_batch_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("Employee")


class EmailRecord(Base):
    __tablename__ = "email_records"
    __table_args__ = (
        Index("idx_email_sender_time", "sender_email", "sent_at"),
        Index("idx_email_message_id", "message_id", unique=True),
        Index("idx_email_has_attachment", "has_attachment"),
        Index("idx_email_external_recipients", "external_recipient_count"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_data_id = Column(UUID(as_uuid=True), ForeignKey("raw_data_records.id"), nullable=True)
    message_id = Column(String(200), nullable=False, unique=True)
    sender_employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    sender_email = Column(String(200), nullable=False, index=True)
    sender_name = Column(String(100), nullable=True)
    recipient_to = Column(JSON, default=list, nullable=True)
    recipient_cc = Column(JSON, default=list, nullable=True)
    recipient_bcc = Column(JSON, default=list, nullable=True)
    external_recipient_count = Column(Integer, default=0, index=True)
    subject = Column(String(500), nullable=True)
    body_preview = Column(Text, nullable=True)
    body_full_path = Column(String(500), nullable=True)
    has_attachment = Column(Boolean, default=False, index=True)
    attachment_count = Column(Integer, default=0)
    attachment_names = Column(JSON, default=list, nullable=True)
    attachment_sizes = Column(JSON, default=list, nullable=True)
    sent_at = Column(DateTime, nullable=False, index=True)
    received_at = Column(DateTime, nullable=True)
    is_read = Column(Boolean, default=False)
    sensitivity_level = Column(String(20), nullable=True)
    is_external = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    sender = relationship("Employee", foreign_keys=[sender_employee_id])


class InstantMessageRecord(Base):
    __tablename__ = "instant_message_records"
    __table_args__ = (
        Index("idx_im_sender_time", "sender_employee_id", "sent_at"),
        Index("idx_im_conversation", "conversation_id"),
        Index("idx_im_message_id", "message_id", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_data_id = Column(UUID(as_uuid=True), ForeignKey("raw_data_records.id"), nullable=True)
    message_id = Column(String(200), nullable=False, unique=True)
    conversation_id = Column(String(200), nullable=True, index=True)
    conversation_name = Column(String(200), nullable=True)
    sender_employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    sender_id = Column(String(100), nullable=True)
    sender_name = Column(String(100), nullable=True)
    message_type = Column(String(20), default="text")
    content = Column(Text, nullable=True)
    content_full_path = Column(String(500), nullable=True)
    has_file = Column(Boolean, default=False)
    file_names = Column(JSON, default=list, nullable=True)
    mentioned_users = Column(JSON, default=list, nullable=True)
    is_external_chat = Column(Boolean, default=False)
    participant_count = Column(Integer, default=2)
    participants = Column(JSON, default=list, nullable=True)
    sent_at = Column(DateTime, nullable=False, index=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    sender = relationship("Employee", foreign_keys=[sender_employee_id])


class DoorAccessRecord(Base):
    __tablename__ = "door_access_records"
    __table_args__ = (
        Index("idx_door_employee_time", "employee_id", "access_time"),
        Index("idx_door_location_time", "location_id", "access_time"),
        Index("idx_door_access_type", "access_type"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_data_id = Column(UUID(as_uuid=True), ForeignKey("raw_data_records.id"), nullable=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    employee_identifier = Column(String(100), nullable=True)
    employee_name = Column(String(100), nullable=True)
    location_id = Column(String(100), nullable=True, index=True)
    location_name = Column(String(200), nullable=True)
    location_type = Column(String(50), nullable=True)
    access_type = Column(String(20), nullable=False, index=True)
    access_method = Column(String(20), nullable=True)
    device_id = Column(String(100), nullable=True)
    access_time = Column(DateTime, nullable=False, index=True)
    is_after_hours = Column(Boolean, default=False)
    is_restricted_area = Column(Boolean, default=False)
    access_result = Column(String(20), default="granted")
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("Employee", foreign_keys=[employee_id])


class FinanceRecord(Base):
    __tablename__ = "finance_records"
    __table_args__ = (
        Index("idx_finance_employee_time", "employee_id", "transaction_date"),
        Index("idx_finance_type_status", "record_type", "status"),
        Index("idx_finance_amount", "amount"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_data_id = Column(UUID(as_uuid=True), ForeignKey("raw_data_records.id"), nullable=True)
    record_type = Column(String(50), nullable=False)
    record_id = Column(String(100), nullable=False, unique=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    employee_identifier = Column(String(100), nullable=True)
    employee_name = Column(String(100), nullable=True)
    department_id = Column(UUID(as_uuid=True), nullable=True)
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    amount = Column(Float, nullable=False, index=True)
    currency = Column(String(10), default="CNY")
    transaction_date = Column(DateTime, nullable=False, index=True)
    submission_date = Column(DateTime, nullable=True)
    approval_date = Column(DateTime, nullable=True)
    reimbursement_date = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False, index=True)
    vendor_name = Column(String(200), nullable=True)
    invoice_count = Column(Integer, default=0)
    invoice_ids = Column(JSON, default=list, nullable=True)
    category = Column(String(100), nullable=True)
    payment_method = Column(String(50), nullable=True)
    approvers = Column(JSON, default=list, nullable=True)
    is_flagged = Column(Boolean, default=False)
    flags = Column(JSON, default=list, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("Employee", foreign_keys=[employee_id])
