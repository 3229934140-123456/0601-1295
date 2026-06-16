from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import re
from enum import Enum
from app.core.constants import SeverityLevel, EventType, DataSourceType


class RuleOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    REGEX = "regex"
    EXISTS = "exists"
    BETWEEN = "between"


@dataclass
class Condition:
    field: str
    operator: RuleOperator
    value: Any
    nested_path: Optional[str] = None

    def evaluate(self, data: Dict[str, Any]) -> bool:
        field_value = self._get_nested_value(data)
        return self._compare(field_value)

    def _get_nested_value(self, data: Dict[str, Any]) -> Any:
        keys = self.field.split(".") if self.nested_path is None else self.nested_path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
                if current is None:
                    return None
            else:
                return None
        return current

    def _compare(self, field_value: Any) -> bool:
        op = self.operator
        target = self.value

        if op == RuleOperator.EXISTS:
            return field_value is not None

        if field_value is None:
            if op in (RuleOperator.NE, RuleOperator.NOT_IN, RuleOperator.NOT_CONTAINS):
                return True
            return False

        try:
            if op == RuleOperator.EQ:
                return str(field_value) == str(target)
            elif op == RuleOperator.NE:
                return str(field_value) != str(target)
            elif op == RuleOperator.GT:
                return float(field_value) > float(target)
            elif op == RuleOperator.GTE:
                return float(field_value) >= float(target)
            elif op == RuleOperator.LT:
                return float(field_value) < float(target)
            elif op == RuleOperator.LTE:
                return float(field_value) <= float(target)
            elif op == RuleOperator.IN:
                return str(field_value).lower() in [str(v).lower() for v in target]
            elif op == RuleOperator.NOT_IN:
                return str(field_value).lower() not in [str(v).lower() for v in target]
            elif op == RuleOperator.CONTAINS:
                if isinstance(field_value, str) and isinstance(target, str):
                    return target.lower() in field_value.lower()
                if isinstance(field_value, list):
                    return any(target.lower() in str(v).lower() for v in field_value)
                return False
            elif op == RuleOperator.NOT_CONTAINS:
                if isinstance(field_value, str) and isinstance(target, str):
                    return target.lower() not in field_value.lower()
                return True
            elif op == RuleOperator.STARTS_WITH:
                return str(field_value).lower().startswith(str(target).lower())
            elif op == RuleOperator.ENDS_WITH:
                return str(field_value).lower().endswith(str(target).lower())
            elif op == RuleOperator.REGEX:
                return bool(re.search(target, str(field_value), re.IGNORECASE))
            elif op == RuleOperator.BETWEEN:
                if isinstance(target, (list, tuple)) and len(target) == 2:
                    return float(target[0]) <= float(field_value) <= float(target[1])
                return False
        except (ValueError, TypeError):
            return False

        return False


@dataclass
class RuleConditionGroup:
    logic: str = "AND"
    conditions: List[Condition] = field(default_factory=list)
    sub_groups: List["RuleConditionGroup"] = field(default_factory=list)

    def evaluate(self, data: Dict[str, Any]) -> bool:
        if not self.conditions and not self.sub_groups:
            return True

        condition_results = [c.evaluate(data) for c in self.conditions]
        group_results = [g.evaluate(data) for g in self.sub_groups]
        all_results = condition_results + group_results

        if self.logic == "AND":
            return all(all_results) if all_results else True
        elif self.logic == "OR":
            return any(all_results) if all_results else False
        elif self.logic == "NOR":
            return not any(all_results) if all_results else True
        return False


@dataclass
class ThresholdConfig:
    count_threshold: Optional[int] = None
    time_window_hours: int = 24
    unique_field: Optional[str] = None
    aggregation_field: Optional[str] = None
    aggregation_operator: Optional[str] = None
    aggregation_threshold: Optional[float] = None


@dataclass
class DetectionRule:
    rule_id: str
    name: str
    description: str
    event_type: EventType
    severity: SeverityLevel
    data_sources: List[DataSourceType]
    condition_group: RuleConditionGroup
    threshold: ThresholdConfig
    score_weight: int = 10
    is_active: bool = True

    def matches_data_source(self, source: DataSourceType) -> bool:
        return source in self.data_sources


def build_default_rules() -> List[DetectionRule]:
    return [
        DetectionRule(
            rule_id="RULE-EMAIL-001",
            name="向外部发送大量敏感附件",
            description="检测员工向外部邮箱发送带敏感关键词且有大附件的邮件",
            event_type=EventType.DATA_LEAK,
            severity=SeverityLevel.CRITICAL,
            data_sources=[DataSourceType.EMAIL],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition("is_external", RuleOperator.EQ, True),
                    Condition("external_recipient_count", RuleOperator.GTE, 1),
                    Condition("has_attachment", RuleOperator.EQ, True),
                    Condition("attachment_sizes", RuleOperator.GTE, 1024 * 1024),
                    Condition(field="sensitivity_level", operator=RuleOperator.EQ, value="high"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=1, time_window_hours=1),
            score_weight=90,
        ),
        DetectionRule(
            rule_id="RULE-EMAIL-002",
            name="下班后发送敏感信息",
            description="检测非正常工作时间发送带敏感关键词的邮件",
            event_type=EventType.SUSPICIOUS_COMMUNICATION,
            severity=SeverityLevel.IMPORTANT,
            data_sources=[DataSourceType.EMAIL],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition(field="sensitivity_level", operator=RuleOperator.EQ, value="high"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=3, time_window_hours=24),
            score_weight=50,
        ),
        DetectionRule(
            rule_id="RULE-IM-001",
            name="即时通讯中出现违规关键词",
            description="检测聊天中出现回扣、好处费、飞单、违规、漏税等敏感词",
            event_type=EventType.FRAUD,
            severity=SeverityLevel.CRITICAL,
            data_sources=[DataSourceType.INSTANT_MESSAGE],
            condition_group=RuleConditionGroup(
                logic="OR",
                conditions=[
                    Condition("content", RuleOperator.CONTAINS, "回扣"),
                    Condition("content", RuleOperator.CONTAINS, "好处费"),
                    Condition("content", RuleOperator.CONTAINS, "私单"),
                    Condition("content", RuleOperator.CONTAINS, "飞单"),
                    Condition("content", RuleOperator.CONTAINS, "漏税"),
                    Condition("content", RuleOperator.CONTAINS, "洗钱"),
                    Condition("content", RuleOperator.CONTAINS, "不要记录"),
                    Condition("content", RuleOperator.CONTAINS, "别留记录"),
                    Condition("content", RuleOperator.CONTAINS, "bribe"),
                    Condition("content", RuleOperator.CONTAINS, "kickback"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=1, time_window_hours=1),
            score_weight=85,
        ),
        DetectionRule(
            rule_id="RULE-IM-002",
            name="短时间内大量删除消息",
            description="检测员工在短时间内删除多条聊天消息",
            event_type=EventType.ABNORMAL_BEHAVIOR,
            severity=SeverityLevel.IMPORTANT,
            data_sources=[DataSourceType.INSTANT_MESSAGE],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition("is_deleted", RuleOperator.EQ, True),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=5, time_window_hours=1, unique_field="sender_employee_id"),
            score_weight=45,
        ),
        DetectionRule(
            rule_id="RULE-IM-003",
            name="聊天中出现内部信息泄露词",
            description="检测出现机密信息、股价、收购、并购等内部敏感词",
            event_type=EventType.DATA_LEAK,
            severity=SeverityLevel.CRITICAL,
            data_sources=[DataSourceType.INSTANT_MESSAGE],
            condition_group=RuleConditionGroup(
                logic="OR",
                conditions=[
                    Condition("content", RuleOperator.CONTAINS, "机密信息"),
                    Condition("content", RuleOperator.CONTAINS, "内部信息"),
                    Condition("content", RuleOperator.CONTAINS, "股价"),
                    Condition("content", RuleOperator.CONTAINS, "收购"),
                    Condition("content", RuleOperator.CONTAINS, "并购"),
                    Condition("content", RuleOperator.CONTAINS, "confidential"),
                    Condition("content", RuleOperator.CONTAINS, "insider"),
                    Condition("content", RuleOperator.CONTAINS, "stock tip"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=1, time_window_hours=1),
            score_weight=80,
        ),
        DetectionRule(
            rule_id="RULE-IM-004",
            name="出现职场骚扰或歧视言论",
            description="检测聊天中出现骚扰、歧视、威胁等不当言论",
            event_type=EventType.HARASSMENT,
            severity=SeverityLevel.CRITICAL,
            data_sources=[DataSourceType.INSTANT_MESSAGE],
            condition_group=RuleConditionGroup(
                logic="OR",
                conditions=[
                    Condition("content", RuleOperator.CONTAINS, "骚扰"),
                    Condition("content", RuleOperator.CONTAINS, "低俗"),
                    Condition("content", RuleOperator.CONTAINS, "黄色"),
                    Condition("content", RuleOperator.CONTAINS, "威胁"),
                    Condition("content", RuleOperator.CONTAINS, "恐吓"),
                    Condition("content", RuleOperator.CONTAINS, "歧视"),
                    Condition("content", RuleOperator.CONTAINS, "harass"),
                    Condition("content", RuleOperator.CONTAINS, "threat"),
                    Condition("content", RuleOperator.CONTAINS, "abuse"),
                    Condition("content", RuleOperator.CONTAINS, "discriminat"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=1, time_window_hours=1),
            score_weight=95,
        ),
        DetectionRule(
            rule_id="RULE-DOOR-001",
            name="非工作时间访问限制区域",
            description="检测非工作时间(20:00-次日8:00)进入服务器机房、财务室等限制区域",
            event_type=EventType.UNAUTHORIZED_ACCESS,
            severity=SeverityLevel.IMPORTANT,
            data_sources=[DataSourceType.DOOR_ACCESS],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition("is_after_hours", RuleOperator.EQ, True),
                    Condition("is_restricted_area", RuleOperator.EQ, True),
                    Condition("access_result", RuleOperator.EQ, "granted"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=1, time_window_hours=1),
            score_weight=60,
        ),
        DetectionRule(
            rule_id="RULE-DOOR-002",
            name="频繁门禁被拒异常",
            description="检测短时间内多次尝试门禁失败",
            event_type=EventType.UNAUTHORIZED_ACCESS,
            severity=SeverityLevel.IMPORTANT,
            data_sources=[DataSourceType.DOOR_ACCESS],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition("access_result", RuleOperator.EQ, "denied"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=3, time_window_hours=1, unique_field="employee_identifier"),
            score_weight=55,
        ),
        DetectionRule(
            rule_id="RULE-DOOR-003",
            name="限制区域短时间多次进出",
            description="检测在限制区域内短时间多次刷卡进出",
            event_type=EventType.ABNORMAL_BEHAVIOR,
            severity=SeverityLevel.GENERAL,
            data_sources=[DataSourceType.DOOR_ACCESS],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition("is_restricted_area", RuleOperator.EQ, True),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=5, time_window_hours=2, unique_field="employee_identifier"),
            score_weight=35,
        ),
        DetectionRule(
            rule_id="RULE-FIN-001",
            name="报销金额远超阈值",
            description="单次报销金额显著超过同类报销上限",
            event_type=EventType.FRAUD,
            severity=SeverityLevel.CRITICAL,
            data_sources=[DataSourceType.FINANCE],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition("is_flagged", RuleOperator.EQ, True),
                    Condition("flags", RuleOperator.CONTAINS, "high_amount"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=1, time_window_hours=24),
            score_weight=70,
        ),
        DetectionRule(
            rule_id="RULE-FIN-002",
            name="整数额报销异常",
            description="检测报销金额恰好为大额整数的可疑报销",
            event_type=EventType.FRAUD,
            severity=SeverityLevel.IMPORTANT,
            data_sources=[DataSourceType.FINANCE],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition("flags", RuleOperator.CONTAINS, "round_number_amount"),
                    Condition("amount", RuleOperator.GTE, 3000),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=2, time_window_hours=72),
            score_weight=50,
        ),
        DetectionRule(
            rule_id="RULE-FIN-003",
            name="礼品、咨询、劳务费合规审查",
            description="礼品费、咨询费、劳务费等高风险类别自动标记审查",
            event_type=EventType.CONFLICT_OF_INTEREST,
            severity=SeverityLevel.IMPORTANT,
            data_sources=[DataSourceType.FINANCE],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[
                    Condition("flags", RuleOperator.CONTAINS, "compliance_review_required"),
                ]
            ),
            threshold=ThresholdConfig(count_threshold=1, time_window_hours=24),
            score_weight=55,
        ),
        DetectionRule(
            rule_id="RULE-FIN-004",
            name="短时间高频报销",
            description="检测员工短时间内提交多笔报销",
            event_type=EventType.FRAUD,
            severity=SeverityLevel.GENERAL,
            data_sources=[DataSourceType.FINANCE],
            condition_group=RuleConditionGroup(
                logic="AND",
                conditions=[]
            ),
            threshold=ThresholdConfig(
                count_threshold=5,
                time_window_hours=24,
                unique_field="employee_identifier",
                aggregation_field="amount",
                aggregation_operator="sum",
                aggregation_threshold=10000
            ),
            score_weight=40,
        ),
    ]
