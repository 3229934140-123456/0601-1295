from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean,
    Text, ForeignKey, Index, JSON, Float, Date
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, ARRAY
import uuid
from app.core.database import Base


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        Index("idx_department_code", "code", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    parent = relationship("Department", remote_side=[id], backref="children")
    employees = relationship("Employee", foreign_keys="Employee.department_id", back_populates="department")


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        Index("idx_employee_emp_id", "employee_id", unique=True),
        Index("idx_employee_department", "department_id"),
        Index("idx_employee_status", "employment_status"),
        Index("idx_employee_name", "name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = Column(String(50), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    gender = Column(String(10), nullable=True)
    email = Column(String(200), nullable=False, unique=True)
    phone = Column(String(20), nullable=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=False, index=True)
    position = Column(String(100), nullable=True)
    job_level = Column(String(50), nullable=True)
    employment_status = Column(String(20), default="active", index=True)
    hire_date = Column(Date, nullable=True)
    resignation_date = Column(Date, nullable=True)
    supervisor_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    permissions = Column(JSON, default=list, nullable=True)
    extra_info = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    department = relationship("Department", foreign_keys=[department_id], back_populates="employees")
    supervisor = relationship("Employee", remote_side=[id])
    compliance_profile = relationship("ComplianceProfile", uselist=False, back_populates="employee")
    tickets_as_subject = relationship("InvestigationTicket", foreign_keys="InvestigationTicket.subject_employee_id", back_populates="subject_employee")
    tickets_as_officer = relationship("InvestigationTicket", foreign_keys="InvestigationTicket.assigned_officer_id", back_populates="assigned_officer")


class InvestigationOfficer(Base):
    __tablename__ = "investigation_officers"
    __table_args__ = (
        Index("idx_officer_employee", "employee_id", unique=True),
        Index("idx_officer_specialization", "specializations"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=False, unique=True)
    specializations = Column(ARRAY(String(100)), default=list, nullable=True)
    departments_covered = Column(ARRAY(UUID(as_uuid=True)), default=list, nullable=True)
    current_ticket_count = Column(Integer, default=0)
    max_ticket_capacity = Column(Integer, default=10)
    is_available = Column(Boolean, default=True)
    supervisor_id = Column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("Employee", foreign_keys=[employee_id])
    supervisor = relationship("Employee", foreign_keys=[supervisor_id])
