from enum import Enum
from datetime import timedelta


class SeverityLevel(str, Enum):
    GENERAL = "general"
    IMPORTANT = "important"
    CRITICAL = "critical"


class SeverityLevelCN(str, Enum):
    GENERAL = "一般"
    IMPORTANT = "重要"
    CRITICAL = "重大"


SEVERITY_TIME_LIMIT = {
    SeverityLevel.GENERAL: timedelta(days=7),
    SeverityLevel.IMPORTANT: timedelta(days=3),
    SeverityLevel.CRITICAL: timedelta(hours=48),
}

SEVERITY_LEVEL_MAP = {
    SeverityLevel.GENERAL: 1,
    SeverityLevel.IMPORTANT: 2,
    SeverityLevel.CRITICAL: 3,
}


class EventType(str, Enum):
    DATA_LEAK = "data_leak"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    FRAUD = "fraud"
    CONFLICT_OF_INTEREST = "conflict_of_interest"
    HARASSMENT = "harassment"
    DISCRIMINATION = "discrimination"
    THEFT = "theft"
    POLICY_VIOLATION = "policy_violation"
    SUSPICIOUS_COMMUNICATION = "suspicious_communication"
    ABNORMAL_BEHAVIOR = "abnormal_behavior"
    OTHER = "other"


class EventTypeCN(str, Enum):
    DATA_LEAK = "数据泄露"
    UNAUTHORIZED_ACCESS = "未授权访问"
    FRAUD = "财务欺诈"
    CONFLICT_OF_INTEREST = "利益冲突"
    HARASSMENT = "职场骚扰"
    DISCRIMINATION = "歧视行为"
    THEFT = "盗窃行为"
    POLICY_VIOLATION = "违反制度"
    SUSPICIOUS_COMMUNICATION = "可疑通讯"
    ABNORMAL_BEHAVIOR = "异常行为"
    OTHER = "其他"


class TicketStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    UNDER_INVESTIGATION = "under_investigation"
    EVIDENCE_COLLECTED = "evidence_collected"
    CONCLUSION_SUBMITTED = "conclusion_submitted"
    UNDER_APPROVAL = "under_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CLOSED = "closed"
    ESCALATED = "escalated"


class TicketStatusCN(str, Enum):
    PENDING = "待分配"
    ASSIGNED = "已分配"
    UNDER_INVESTIGATION = "调查中"
    EVIDENCE_COLLECTED = "已取证"
    CONCLUSION_SUBMITTED = "待审批"
    UNDER_APPROVAL = "审批中"
    APPROVED = "审批通过"
    REJECTED = "审批驳回"
    CLOSED = "已关闭"
    ESCALATED = "已升级"


class ViolationResult(str, Enum):
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    FALSE_POSITIVE = "false_positive"
    PENDING = "pending"


class DisciplinaryAction(str, Enum):
    WARNING = "warning"
    SERIOUS_WARNING = "serious_warning"
    DEMOTION = "demotion"
    SALARY_REDUCTION = "salary_reduction"
    PERMISSION_FREEZE = "permission_freeze"
    TERMINATION = "termination"
    TRAINING = "training"
    NO_ACTION = "no_action"


class DisciplinaryActionCN(str, Enum):
    WARNING = "警告"
    SERIOUS_WARNING = "记过"
    DEMOTION = "降级"
    SALARY_REDUCTION = "降薪"
    PERMISSION_FREEZE = "冻结权限"
    TERMINATION = "解除合同"
    TRAINING = "合规培训"
    NO_ACTION = "无处罚"


class DataSourceType(str, Enum):
    EMAIL = "email"
    INSTANT_MESSAGE = "instant_message"
    DOOR_ACCESS = "door_access"
    FINANCE = "finance"
    EMPLOYEE_REPORT = "employee_report"
    MANUAL_ENTRY = "manual_entry"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class InvestigationConclusion(str, Enum):
    GUILTY = "guilty"
    NOT_GUILTY = "not_guilty"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FALSE_ALARM = "false_alarm"


class LogActionType(str, Enum):
    DATA_COLLECTION = "data_collection"
    EVENT_DETECTED = "event_detected"
    EVENT_CLASSIFIED = "event_classified"
    TICKET_CREATED = "ticket_created"
    TICKET_ASSIGNED = "ticket_assigned"
    EVIDENCE_COLLECTED = "evidence_collected"
    CONCLUSION_SUBMITTED = "conclusion_submitted"
    APPROVAL_STARTED = "approval_started"
    APPROVAL_DECISION = "approval_decision"
    ACTION_EXECUTED = "action_executed"
    TICKET_ESCALATED = "ticket_escalated"
    TICKET_CLOSED = "ticket_closed"
    REPORT_GENERATED = "report_generated"
    USER_LOGIN = "user_login"
    USER_QUERY = "user_query"
    DATA_EXPORT = "data_export"
