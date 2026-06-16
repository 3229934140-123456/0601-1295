from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import uuid
import hashlib
import json
from collections import defaultdict, Counter
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import logger
from app.core.constants import (
    SeverityLevel, EventType, DataSourceType,
    TicketStatus
)
from app.core.database import get_db_context
from app.models.investigation import ComplianceEvent, ComplianceRule
from app.models.data_source import (
    RawDataRecord, EmailRecord, InstantMessageRecord,
    DoorAccessRecord, FinanceRecord
)
from app.models.organization import Employee
from .rules import DetectionRule, build_default_rules, Condition, RuleConditionGroup, ThresholdConfig


class ViolationDetectionEngine:
    def __init__(self):
        self.rules: List[DetectionRule] = []
        self.logger = logger.bind(module="ViolationDetectionEngine")
        self._event_counter = defaultdict(int)

    async def initialize(self):
        await self._load_rules_from_db()
        if not self.rules:
            await self._bootstrap_default_rules()
            await self._load_rules_from_db()
        self.logger.info(
            "Detection engine initialized",
            active_rules=len([r for r in self.rules if r.is_active])
        )

    async def _load_rules_from_db(self):
        self.rules = []
        async with get_db_context() as db:
            result = await db.execute(
                select(ComplianceRule).where(ComplianceRule.is_active == True)
            )
            db_rules = result.scalars().all()

            for db_rule in db_rules:
                try:
                    conditions_data = db_rule.conditions or {}
                    condition_group = self._deserialize_condition_group(conditions_data)

                    threshold_data = db_rule.threshold_config or {}
                    threshold = ThresholdConfig(**threshold_data)

                    rule = DetectionRule(
                        rule_id=db_rule.rule_code,
                        name=db_rule.name,
                        description=db_rule.description or "",
                        event_type=EventType(db_rule.event_type),
                        severity=SeverityLevel(db_rule.severity),
                        data_sources=[DataSourceType(ds) for ds in (db_rule.data_sources or [])],
                        condition_group=condition_group,
                        threshold=threshold,
                        score_weight=db_rule.score_weight,
                        is_active=db_rule.is_active,
                    )
                    self.rules.append(rule)
                except Exception as e:
                    self.logger.error(
                        "Failed to load rule",
                        rule_code=db_rule.rule_code,
                        error=str(e)
                    )

    @staticmethod
    def _deserialize_condition_group(data: Dict[str, Any]) -> RuleConditionGroup:
        group = RuleConditionGroup(logic=data.get("logic", "AND"))

        for cond_data in data.get("conditions", []):
            condition = Condition(
                field=cond_data["field"],
                operator=cond_data["operator"],
                value=cond_data["value"],
                nested_path=cond_data.get("nested_path")
            )
            group.conditions.append(condition)

        for sub_data in data.get("sub_groups", []):
            group.sub_groups.append(
                ViolationDetectionEngine._deserialize_condition_group(sub_data)
            )

        return group

    async def _bootstrap_default_rules(self):
        default_rules = build_default_rules()
        async with get_db_context() as db:
            for rule in default_rules:
                existing = await db.execute(
                    select(ComplianceRule).where(ComplianceRule.rule_code == rule.rule_id)
                )
                if existing.scalar_one_or_none():
                    continue

                conditions_data = self._serialize_condition_group(rule.condition_group)
                threshold_data = {
                    "count_threshold": rule.threshold.count_threshold,
                    "time_window_hours": rule.threshold.time_window_hours,
                    "unique_field": rule.threshold.unique_field,
                    "aggregation_field": rule.threshold.aggregation_field,
                    "aggregation_operator": rule.threshold.aggregation_operator,
                    "aggregation_threshold": rule.threshold.aggregation_threshold,
                }

                db_rule = ComplianceRule(
                    id=uuid.uuid4(),
                    rule_code=rule.rule_id,
                    name=rule.name,
                    description=rule.description,
                    event_type=rule.event_type.value,
                    severity=rule.severity.value,
                    data_sources=[ds.value for ds in rule.data_sources],
                    conditions=conditions_data,
                    threshold_config=threshold_data,
                    score_weight=rule.score_weight,
                    is_active=True,
                    version="1.0",
                )
                db.add(db_rule)

            self.logger.info(
                "Bootstrapped default compliance rules",
                count=len(default_rules)
            )

    @staticmethod
    def _serialize_condition_group(group: RuleConditionGroup) -> Dict[str, Any]:
        return {
            "logic": group.logic,
            "conditions": [
                {
                    "field": c.field,
                    "operator": c.operator.value if isinstance(c.operator, type) else c.operator,
                    "value": c.value,
                    "nested_path": c.nested_path,
                }
                for c in group.conditions
            ],
            "sub_groups": [
                ViolationDetectionEngine._serialize_condition_group(sg)
                for sg in group.sub_groups
            ],
        }

    async def detect_violations(self, lookback_hours: int = 24) -> List[ComplianceEvent]:
        await self.initialize()

        all_events: List[ComplianceEvent] = []
        window_start = datetime.utcnow() - timedelta(hours=lookback_hours)

        source_processors = [
            (DataSourceType.EMAIL, self._process_email_source),
            (DataSourceType.INSTANT_MESSAGE, self._process_im_source),
            (DataSourceType.DOOR_ACCESS, self._process_door_source),
            (DataSourceType.FINANCE, self._process_finance_source),
        ]

        for source_type, processor in source_processors:
            try:
                applicable_rules = [r for r in self.rules if r.matches_data_source(source_type)]
                if not applicable_rules:
                    continue

                events = await processor(applicable_rules, window_start)
                all_events.extend(events)
                self.logger.info(
                    "Source detection complete",
                    source=source_type.value,
                    events_found=len(events),
                    rules_checked=len(applicable_rules)
                )
            except Exception as e:
                self.logger.error(
                    "Source detection failed",
                    source=source_type.value,
                    error=str(e)
                )

        deduplicated = await self._deduplicate_events(all_events)
        self.logger.info(
            "Violation detection complete",
            total_found=len(all_events),
            after_deduplication=len(deduplicated)
        )
        return deduplicated

    async def _process_email_source(
        self, rules: List[DetectionRule], window_start: datetime
    ) -> List[ComplianceEvent]:
        events: List[ComplianceEvent] = []
        async with get_db_context() as db:
            result = await db.execute(
                select(EmailRecord).where(EmailRecord.sent_at >= window_start)
            )
            records = result.scalars().all()

        for record in records:
            data = self._email_to_dict(record)
            for rule in rules:
                if rule.condition_group.evaluate(data):
                    event = await self._create_event_candidate(
                        rule, data, DataSourceType.EMAIL, record
                    )
                    events.append(event)

        return await self._apply_threshold_filtering(events, rules, window_start)

    async def _process_im_source(
        self, rules: List[DetectionRule], window_start: datetime
    ) -> List[ComplianceEvent]:
        events: List[ComplianceEvent] = []
        async with get_db_context() as db:
            result = await db.execute(
                select(InstantMessageRecord).where(InstantMessageRecord.sent_at >= window_start)
            )
            records = result.scalars().all()

        for record in records:
            data = self._im_to_dict(record)
            for rule in rules:
                if rule.condition_group.evaluate(data):
                    event = await self._create_event_candidate(
                        rule, data, DataSourceType.INSTANT_MESSAGE, record
                    )
                    events.append(event)

        return await self._apply_threshold_filtering(events, rules, window_start)

    async def _process_door_source(
        self, rules: List[DetectionRule], window_start: datetime
    ) -> List[ComplianceEvent]:
        events: List[ComplianceEvent] = []
        async with get_db_context() as db:
            result = await db.execute(
                select(DoorAccessRecord).where(DoorAccessRecord.access_time >= window_start)
            )
            records = result.scalars().all()

        for record in records:
            data = self._door_to_dict(record)
            for rule in rules:
                if rule.condition_group.evaluate(data):
                    event = await self._create_event_candidate(
                        rule, data, DataSourceType.DOOR_ACCESS, record
                    )
                    events.append(event)

        return await self._apply_threshold_filtering(events, rules, window_start)

    async def _process_finance_source(
        self, rules: List[DetectionRule], window_start: datetime
    ) -> List[ComplianceEvent]:
        events: List[ComplianceEvent] = []
        async with get_db_context() as db:
            result = await db.execute(
                select(FinanceRecord).where(FinanceRecord.transaction_date >= window_start)
            )
            records = result.scalars().all()

        for record in records:
            data = self._finance_to_dict(record)
            for rule in rules:
                if rule.condition_group.evaluate(data):
                    event = await self._create_event_candidate(
                        rule, data, DataSourceType.FINANCE, record
                    )
                    events.append(event)

        return await self._apply_threshold_filtering(events, rules, window_start)

    @staticmethod
    def _email_to_dict(r: EmailRecord) -> Dict[str, Any]:
        return {
            "sender_email": r.sender_email,
            "sender_name": r.sender_name,
            "to": r.recipient_to,
            "cc": r.recipient_cc,
            "bcc": r.recipient_bcc,
            "external_recipient_count": r.external_recipient_count,
            "subject": r.subject or "",
            "body_preview": r.body_preview or "",
            "has_attachment": r.has_attachment,
            "attachment_count": r.attachment_count,
            "attachment_names": r.attachment_names or [],
            "attachment_sizes": max(r.attachment_sizes or [0]) if r.attachment_sizes else 0,
            "sent_at": r.sent_at.isoformat(),
            "sensitivity_level": r.sensitivity_level,
            "is_external": r.is_external,
        }

    @staticmethod
    def _im_to_dict(r: InstantMessageRecord) -> Dict[str, Any]:
        return {
            "conversation_id": r.conversation_id,
            "conversation_name": r.conversation_name or "",
            "sender_id": r.sender_id,
            "sender_name": r.sender_name or "",
            "content": r.content or "",
            "message_type": r.message_type,
            "has_file": r.has_file,
            "file_names": r.file_names or [],
            "mentioned_users": r.mentioned_users or [],
            "is_external_chat": r.is_external_chat,
            "participant_count": r.participant_count,
            "is_deleted": r.is_deleted,
            "sent_at": r.sent_at.isoformat(),
        }

    @staticmethod
    def _door_to_dict(r: DoorAccessRecord) -> Dict[str, Any]:
        return {
            "employee_identifier": r.employee_identifier,
            "employee_name": r.employee_name or "",
            "location_id": r.location_id or "",
            "location_name": r.location_name or "",
            "location_type": r.location_type or "",
            "access_type": r.access_type,
            "access_method": r.access_method or "",
            "access_time": r.access_time.isoformat(),
            "is_after_hours": r.is_after_hours,
            "is_restricted_area": r.is_restricted_area,
            "access_result": r.access_result,
        }

    @staticmethod
    def _finance_to_dict(r: FinanceRecord) -> Dict[str, Any]:
        return {
            "employee_identifier": r.employee_identifier,
            "employee_name": r.employee_name or "",
            "title": r.title or "",
            "description": r.description or "",
            "amount": r.amount,
            "currency": r.currency,
            "transaction_date": r.transaction_date.isoformat(),
            "status": r.status,
            "vendor_name": r.vendor_name or "",
            "invoice_count": r.invoice_count,
            "category": r.category or "",
            "payment_method": r.payment_method or "",
            "is_flagged": r.is_flagged,
            "flags": r.flags or [],
        }

    async def _create_event_candidate(
        self,
        rule: DetectionRule,
        data: Dict[str, Any],
        source: DataSourceType,
        record: Any,
    ) -> ComplianceEvent:
        emp_id = None
        emp_name = None
        dept_id = None
        dept_name = None
        event_time = None
        record_id = getattr(record, "id", None)

        identifier_fields = ["employee_identifier", "sender_email", "sender_id"]
        identifier = None
        for field in identifier_fields:
            if data.get(field):
                identifier = str(data[field])
                break

        if identifier:
            async with get_db_context() as db:
                emp_query = select(Employee).where(
                    or_(
                        Employee.email == identifier.lower() if "@" in identifier else False,
                        Employee.employee_id == identifier,
                    )
                )
                emp_result = await db.execute(emp_query)
                emp = emp_result.scalar_one_or_none()
                if emp:
                    emp_id = emp.id
                    emp_name = emp.name
                    dept_id = emp.department_id
                    if emp.department:
                        dept_name = emp.department.name

        time_fields = ["sent_at", "access_time", "transaction_date", "recorded_at"]
        for field in time_fields:
            if data.get(field):
                try:
                    event_time = datetime.fromisoformat(str(data[field]).replace("Z", "+00:00")).replace(tzinfo=None)
                    break
                except Exception:
                    pass

        if not event_time and hasattr(record, "sent_at"):
            event_time = record.sent_at
        elif not event_time and hasattr(record, "access_time"):
            event_time = record.access_time
        elif not event_time and hasattr(record, "transaction_date"):
            event_time = record.transaction_date

        title_parts = []
        if emp_name:
            title_parts.append(emp_name)
        title_parts.append(f"涉嫌{self._event_type_cn(rule.event_type)}")
        if rule.name:
            title_parts.append(f"- {rule.name}")
        title = " ".join(title_parts)

        confidence = 0.6
        if rule.score_weight >= 80:
            confidence = 0.95
        elif rule.score_weight >= 50:
            confidence = 0.8

        dedupe_input = json.dumps({
            "rule_id": rule.rule_id,
            "employee_id": str(emp_id) if emp_id else identifier,
            "event_date": event_time.strftime("%Y%m%d") if event_time else datetime.utcnow().strftime("%Y%m%d"),
            "data_source": source.value,
            "record_id": str(record_id),
            "event_type": rule.event_type.value,
        }, sort_keys=True, ensure_ascii=False)

        dedupe_hash = hashlib.sha256(dedupe_input.encode()).hexdigest()

        event_counter_key = f"{datetime.utcnow().strftime('%Y%m%d')}_{rule.event_type.value}"
        self._event_counter[event_counter_key] += 1
        event_seq = self._event_counter[event_counter_key]
        event_code = f"EVT-{datetime.utcnow().strftime('%Y%m%d')}-{rule.event_type.value[:3].upper()}-{event_seq:05d}"

        return ComplianceEvent(
            id=uuid.uuid4(),
            event_code=event_code,
            event_type=rule.event_type.value,
            severity=rule.severity.value,
            subject_employee_id=emp_id,
            subject_employee_name=emp_name,
            subject_department_id=dept_id,
            subject_department_name=dept_name,
            detected_at=datetime.utcnow(),
            event_time=event_time,
            title=title,
            description=rule.description,
            evidence_summary=data.get("body_preview") or data.get("content") or data.get("description") or title,
            risk_score=rule.score_weight,
            confidence=confidence,
            matched_data_sources=[source.value],
            raw_data_record_ids=[record_id] if record_id else [],
            raw_data_refs=[{
                "source": source.value,
                "table": record.__tablename__ if hasattr(record, "__tablename__") else source.value,
                "id": str(record_id),
                "summary": title[:200],
            }],
            status="detected",
            deduplication_hash=dedupe_hash,
            detection_version="1.0",
            extra_metadata={
                "rule_id": rule.rule_id,
                "rule_name": rule.name,
                "matched_fields": self._extract_matched_fields(rule, data),
            },
        )

    async def _apply_threshold_filtering(
        self,
        events: List[ComplianceEvent],
        rules: List[DetectionRule],
        window_start: datetime,
    ) -> List[ComplianceEvent]:
        rule_thresholds = {r.rule_id: r.threshold for r in rules}
        rule_groups: Dict[str, List[ComplianceEvent]] = defaultdict(list)

        for event in events:
            rule_id = event.extra_metadata.get("rule_id") if event.extra_metadata else None
            if rule_id:
                rule_groups[rule_id].append(event)

        filtered_events: List[ComplianceEvent] = []

        for rule_id, rule_events in rule_groups.items():
            threshold = rule_thresholds.get(rule_id)
            if not threshold:
                filtered_events.extend(rule_events)
                continue

            count_threshold = threshold.count_threshold or 1

            if threshold.unique_field:
                employee_groups: Dict[str, List[ComplianceEvent]] = defaultdict(list)
                for event in rule_events:
                    key = str(event.subject_employee_id or event.subject_employee_name or "unknown")
                    employee_groups[key].append(event)

                for emp_key, emp_events in employee_groups.items():
                    if len(emp_events) >= count_threshold:
                        if threshold.aggregation_field and threshold.aggregation_threshold:
                            total = sum(
                                (e.extra_metadata.get("matched_fields", {}).get(threshold.aggregation_field, 0) or 0)
                                for e in emp_events
                                if e.extra_metadata
                            )
                            if threshold.aggregation_operator == "sum" and total >= threshold.aggregation_threshold:
                                merged = self._merge_events(emp_events, "员工{emp_key}触发阈值聚合")
                                filtered_events.append(merged)
                        else:
                            merged = self._merge_events(emp_events, f"{len(emp_events)}次触发{rule_id}")
                            filtered_events.append(merged)
            else:
                if len(rule_events) >= count_threshold:
                    merged = self._merge_events(rule_events, f"{len(rule_events)}次触发{rule_id}")
                    filtered_events.append(merged)

        return filtered_events

    @staticmethod
    def _merge_events(events: List[ComplianceEvent], summary_suffix: str) -> ComplianceEvent:
        if not events:
            raise ValueError("Cannot merge empty event list")

        primary = events[0]

        all_raw_ids = []
        all_raw_refs = []
        all_sources = set()

        for e in events:
            all_raw_ids.extend(e.raw_data_record_ids or [])
            all_raw_refs.extend(e.raw_data_refs or [])
            all_sources.update(e.matched_data_sources or [])

        max_risk = max(e.risk_score for e in events)
        avg_conf = sum(e.confidence for e in events) / len(events)
        min_time = min(e.event_time or datetime.utcnow() for e in events)

        dedupe_input = f"merged_{primary.event_type}_{primary.subject_employee_id}_{min_time.strftime('%Y%m%d%H')}_{len(events)}"
        new_hash = hashlib.sha256(dedupe_input.encode()).hexdigest()

        primary.title = f"{primary.title} ({summary_suffix})"
        primary.risk_score = max_risk
        primary.confidence = min(0.99, avg_conf)
        primary.raw_data_record_ids = list(set(all_raw_ids))
        primary.raw_data_refs = all_raw_refs
        primary.matched_data_sources = list(all_sources)
        primary.deduplication_hash = new_hash
        if primary.extra_metadata:
            primary.extra_metadata["merged_count"] = len(events)
            primary.extra_metadata["merged_event_codes"] = [e.event_code for e in events]

        return primary

    async def _deduplicate_events(self, events: List[ComplianceEvent]) -> List[ComplianceEvent]:
        if not events:
            return []

        unique_events: Dict[str, ComplianceEvent] = {}
        duplicate_count = 0

        async with get_db_context() as db:
            for event in events:
                existing_db = await db.execute(
                    select(ComplianceEvent).where(
                        ComplianceEvent.deduplication_hash == event.deduplication_hash
                    )
                )
                if existing_db.scalar_one_or_none():
                    duplicate_count += 1
                    continue

                if event.deduplication_hash in unique_events:
                    duplicate_count += 1
                    continue

                unique_events[event.deduplication_hash] = event

            result = list(unique_events.values())
            for event in result:
                db.add(event)
            await db.flush()

            if duplicate_count:
                self.logger.info("Deduplicated events", duplicates_removed=duplicate_count)

            return result

    @staticmethod
    def _extract_matched_fields(rule: DetectionRule, data: Dict[str, Any]) -> Dict[str, Any]:
        matched = {}
        for cond in rule.condition_group.conditions:
            try:
                val = data.get(cond.field)
                matched[cond.field] = val
            except Exception:
                pass
        return matched

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
            EventType.OTHER: "违规行为",
        }
        return cn_map.get(event_type, "违规行为")
