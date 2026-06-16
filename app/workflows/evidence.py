from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import uuid
import os
import json
import hashlib
from collections import defaultdict
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import logger, settings
from app.core.constants import (
    SeverityLevel, DataSourceType, LogActionType
)
from app.core.database import get_db_context
from app.models.investigation import (
    InvestigationTicket, EvidenceItem, EvidencePackage,
    ComplianceEvent
)
from app.models.data_source import (
    EmailRecord, InstantMessageRecord, DoorAccessRecord, FinanceRecord
)
from app.models.compliance import EventTimeline, SystemLog


class EvidenceCollectionService:
    EVIDENCE_WINDOW_BEFORE = timedelta(days=7)
    EVIDENCE_WINDOW_AFTER = timedelta(days=2)

    def __init__(self):
        self.logger = logger.bind(module="EvidenceCollectionService")
        os.makedirs(settings.EVIDENCE_PACKAGE_DIR, exist_ok=True)

    async def collect_evidence_for_ticket(
        self, ticket_id: uuid.UUID
    ) -> EvidencePackage:
        async with get_db_context() as db:
            ticket_result = await db.execute(
                select(InvestigationTicket).where(
                    InvestigationTicket.id == ticket_id
                )
            )
            ticket = ticket_result.scalar_one_or_none()
            if not ticket:
                raise ValueError(f"Ticket not found: {ticket_id}")

            package = await self._get_or_create_package(db, ticket)
            existing_count = await self._get_existing_evidence_count(db, ticket.id)

            if existing_count > 0:
                self.logger.info(
                    "Evidence already exists, appending new findings",
                    ticket_number=ticket.ticket_number,
                    existing=existing_count
                )

            timeline_window = await self._determine_time_window(db, ticket)
            emp_ids = self._collect_related_employees(ticket)

            evidence_items: List[EvidenceItem] = []

            evidence_items.extend(await self._collect_email_evidence(
                db, ticket, emp_ids, timeline_window
            ))
            evidence_items.extend(await self._collect_im_evidence(
                db, ticket, emp_ids, timeline_window
            ))
            evidence_items.extend(await self._collect_door_evidence(
                db, ticket, emp_ids, timeline_window
            ))
            evidence_items.extend(await self._collect_finance_evidence(
                db, ticket, emp_ids, timeline_window
            ))

            for item in evidence_items:
                db.add(item)

            await db.flush()

            timeline_entries = await self._build_event_timeline(
                db, ticket, emp_ids, timeline_window
            )
            for entry in timeline_entries:
                db.add(entry)

            await self._finalize_package(db, package, evidence_items, timeline_entries)

            log = SystemLog(
                id=uuid.uuid4(),
                log_level="INFO",
                action_type=LogActionType.EVIDENCE_COLLECTED.value,
                target_type="evidence_package",
                target_id=package.id,
                target_name=package.package_code,
                action_details={
                    "ticket_id": str(ticket.id),
                    "ticket_number": ticket.ticket_number,
                    "evidence_count": len(evidence_items),
                    "timeline_entries": len(timeline_entries),
                },
                status="success",
            )
            db.add(log)

            self.logger.info(
                "Evidence collection complete",
                ticket_number=ticket.ticket_number,
                evidence_count=len(evidence_items),
                timeline_entries=len(timeline_entries)
            )
            return package

    async def _get_or_create_package(
        self, db: AsyncSession, ticket: InvestigationTicket
    ) -> EvidencePackage:
        existing = await db.execute(
            select(EvidencePackage).where(
                EvidencePackage.ticket_id == ticket.id
            )
        )
        package = existing.scalar_one_or_none()

        if package:
            package.status = "regenerating"
            package.generation_started_at = datetime.utcnow()
            return package

        counter = await self._get_next_package_counter(db)
        package_code = f"EVP-{datetime.utcnow().strftime('%Y%m%d')}-{counter:05d}"

        package = EvidencePackage(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            package_code=package_code,
            status="generating",
            generation_started_at=datetime.utcnow(),
        )
        db.add(package)
        await db.flush()
        return package

    async def _get_next_package_counter(self, db: AsyncSession) -> int:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(EvidencePackage).where(
                EvidencePackage.created_at >= today_start
            )
        )
        return len(result.scalars().all()) + 1

    async def _get_existing_evidence_count(self, db: AsyncSession, ticket_id: uuid.UUID) -> int:
        result = await db.execute(
            select(EvidenceItem).where(EvidenceItem.ticket_id == ticket_id)
        )
        return len(result.scalars().all())

    async def _determine_time_window(
        self, db: AsyncSession, ticket: InvestigationTicket
    ) -> Tuple[datetime, datetime]:
        events_result = await db.execute(
            select(ComplianceEvent).where(ComplianceEvent.ticket_id == ticket.id)
        )
        events = events_result.scalars().all()

        if not events:
            now = datetime.utcnow()
            return now - self.EVIDENCE_WINDOW_BEFORE, now + self.EVIDENCE_WINDOW_AFTER

        event_times = [e.event_time or e.detected_at for e in events if e.event_time or e.detected_at]
        if not event_times:
            now = datetime.utcnow()
            return now - self.EVIDENCE_WINDOW_BEFORE, now + self.EVIDENCE_WINDOW_AFTER

        min_time = min(event_times) - self.EVIDENCE_WINDOW_BEFORE
        max_time = max(event_times) + self.EVIDENCE_WINDOW_AFTER

        return min_time, max_time

    @staticmethod
    def _collect_related_employees(ticket: InvestigationTicket) -> set:
        emp_ids = set()
        if ticket.subject_employee_id:
            emp_ids.add(ticket.subject_employee_id)
        return emp_ids

    async def _collect_email_evidence(
        self,
        db: AsyncSession,
        ticket: InvestigationTicket,
        emp_ids: set,
        time_window: Tuple[datetime, datetime]
    ) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        start_time, end_time = time_window

        query = select(EmailRecord).where(
            EmailRecord.sent_at.between(start_time, end_time)
        )

        conditions = []
        for emp_id in emp_ids:
            emp_result = await db.execute(select(Employee).where(Employee.id == emp_id))
            emp = emp_result.scalar_one_or_none()
            if emp and emp.email:
                conditions.append(EmailRecord.sender_email.ilike(emp.email.lower()))

        if conditions:
            query = query.where(or_(*conditions))

        result = await db.execute(query.limit(200))
        records = result.scalars().all()

        for record in records:
            relevance_score = 0
            if record.external_recipient_count > 0:
                relevance_score += 20
            if record.sensitivity_level:
                relevance_score += 40
            if record.has_attachment:
                relevance_score += 20
            if record.attachment_sizes:
                max_size = max(record.attachment_sizes or [0])
                if max_size > 1024 * 1024:
                    relevance_score += 20

            item = EvidenceItem(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                evidence_type="email",
                data_source=DataSourceType.EMAIL.value,
                source_record_id=record.id,
                title=f"邮件: {record.subject or '(无主题)'}",
                description=f"发件人: {record.sender_name or record.sender_email}",
                content_summary=(
                    f"主题: {record.subject or '无主题'}\n"
                    f"收件人(To): {', '.join(record.recipient_to[:5])}\n"
                    f"附件数: {record.attachment_count}\n"
                    f"预览: {(record.body_preview or '')[:500]}"
                ),
                related_employee_ids=list(emp_ids),
                event_timestamp=record.sent_at,
                is_key_evidence=(relevance_score >= 60),
                relevance_score=min(100, relevance_score),
                chain_of_custody=[{
                    "action": "auto_collected",
                    "timestamp": datetime.utcnow().isoformat(),
                    "source": "evidence_engine",
                }],
            )
            items.append(item)

        return items

    async def _collect_im_evidence(
        self,
        db: AsyncSession,
        ticket: InvestigationTicket,
        emp_ids: set,
        time_window: Tuple[datetime, datetime]
    ) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        start_time, end_time = time_window

        query = select(InstantMessageRecord).where(
            InstantMessageRecord.sent_at.between(start_time, end_time)
        )

        if emp_ids:
            query = query.where(InstantMessageRecord.sender_employee_id.in_(list(emp_ids)))

        result = await db.execute(query.limit(300))
        records = result.scalars().all()

        conv_groups: Dict[str, List[InstantMessageRecord]] = defaultdict(list)
        for record in records:
            conv_id = record.conversation_id or record.id
            conv_groups[conv_id].append(record)

        for conv_id, conv_records in conv_groups.items():
            conv_records.sort(key=lambda r: r.sent_at)
            relevance_score = 0
            suspicious_keywords = ["回扣", "好处费", "飞单", "不要记录", "机密", "违规"]
            for r in conv_records:
                if any(kw in (r.content or "") for kw in suspicious_keywords):
                    relevance_score += 30
                    break
            if any(r.is_deleted for r in conv_records):
                relevance_score += 25
            if any(r.is_external_chat for r in conv_records):
                relevance_score += 15
            if len(conv_records) > 20:
                relevance_score += 10

            first_record = conv_records[0]
            combined_content = "\n".join([
                f"[{r.sent_at.strftime('%H:%M')}] {r.sender_name or '未知'}: {r.content or ''}"
                for r in conv_records[:50]
            ])

            item = EvidenceItem(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                evidence_type="instant_message_conversation",
                data_source=DataSourceType.INSTANT_MESSAGE.value,
                source_record_id=first_record.id,
                title=f"聊天记录: {first_record.conversation_name or '会话' + conv_id[-6:]}",
                description=(
                    f"参与人数: {first_record.participant_count}, "
                    f"消息数: {len(conv_records)}, "
                    f"时间范围: {conv_records[0].sent_at.strftime('%m-%d %H:%M')} ~ "
                    f"{conv_records[-1].sent_at.strftime('%m-%d %H:%M')}"
                ),
                content_summary=combined_content,
                related_employee_ids=list(emp_ids),
                event_timestamp=conv_records[0].sent_at,
                is_key_evidence=(relevance_score >= 50),
                relevance_score=min(100, relevance_score),
                chain_of_custody=[{
                    "action": "auto_collected",
                    "timestamp": datetime.utcnow().isoformat(),
                    "source": "evidence_engine",
                    "message_count": len(conv_records),
                }],
                extra_metadata={
                    "conversation_id": conv_id,
                    "message_ids": [str(r.id) for r in conv_records],
                },
            )
            items.append(item)

        return items

    async def _collect_door_evidence(
        self,
        db: AsyncSession,
        ticket: InvestigationTicket,
        emp_ids: set,
        time_window: Tuple[datetime, datetime]
    ) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        start_time, end_time = time_window

        query = select(DoorAccessRecord).where(
            DoorAccessRecord.access_time.between(start_time, end_time)
        )

        if emp_ids:
            query = query.where(DoorAccessRecord.employee_id.in_(list(emp_ids)))

        result = await db.execute(query.order_by(DoorAccessRecord.access_time).limit(500))
        records = result.scalars().all()

        if not records:
            return items

        abnormal_records = []
        normal_records = []
        for r in records:
            if r.is_restricted_area or r.is_after_hours or r.access_result == "denied":
                abnormal_records.append(r)
            else:
                normal_records.append(r)

        for record in abnormal_records:
            relevance_score = 0
            if record.is_restricted_area:
                relevance_score += 50
            if record.is_after_hours:
                relevance_score += 30
            if record.access_result == "denied":
                relevance_score += 40

            item = EvidenceItem(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                evidence_type="door_access",
                data_source=DataSourceType.DOOR_ACCESS.value,
                source_record_id=record.id,
                title=f"门禁记录: {record.location_name} - {record.access_type}",
                description=(
                    f"员工: {record.employee_name or '未知'}\n"
                    f"地点: {record.location_name} ({record.location_id})\n"
                    f"类型: {record.access_type}\n"
                    f"方式: {record.access_method or '未知'}\n"
                    f"结果: {'成功' if record.access_result == 'granted' else '被拒'}"
                ),
                related_employee_ids=list(emp_ids),
                event_timestamp=record.access_time,
                is_key_evidence=True,
                relevance_score=min(100, relevance_score),
                chain_of_custody=[{
                    "action": "auto_collected",
                    "timestamp": datetime.utcnow().isoformat(),
                    "source": "evidence_engine",
                }],
                extra_metadata={
                    "is_restricted": record.is_restricted_area,
                    "is_after_hours": record.is_after_hours,
                },
            )
            items.append(item)

        if normal_records:
            summary_lines = []
            for r in normal_records[:100]:
                summary_lines.append(
                    f"[{r.access_time.strftime('%m-%d %H:%M')}] "
                    f"{r.location_name} - {r.access_type}"
                )

            item = EvidenceItem(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                evidence_type="door_access_schedule",
                data_source=DataSourceType.DOOR_ACCESS.value,
                title=f"门禁时间线汇总 ({len(normal_records)}条记录)",
                description=f"正常工作时段的门禁记录汇总",
                content_summary="\n".join(summary_lines),
                related_employee_ids=list(emp_ids),
                event_timestamp=normal_records[0].access_time,
                is_key_evidence=False,
                relevance_score=20,
                chain_of_custody=[{
                    "action": "auto_collected",
                    "timestamp": datetime.utcnow().isoformat(),
                    "source": "evidence_engine",
                }],
            )
            items.append(item)

        return items

    async def _collect_finance_evidence(
        self,
        db: AsyncSession,
        ticket: InvestigationTicket,
        emp_ids: set,
        time_window: Tuple[datetime, datetime]
    ) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        start_time, end_time = time_window

        query = select(FinanceRecord).where(
            FinanceRecord.transaction_date.between(start_time, end_time)
        )

        if emp_ids:
            query = query.where(FinanceRecord.employee_id.in_(list(emp_ids)))

        result = await db.execute(query.limit(200))
        records = result.scalars().all()

        flagged_records = [r for r in records if r.is_flagged]
        normal_records = [r for r in records if not r.is_flagged]

        for record in flagged_records:
            relevance_score = 0
            if "high_amount" in (record.flags or []):
                relevance_score += 40
            if "round_number_amount" in (record.flags or []):
                relevance_score += 30
            if "compliance_review_required" in (record.flags or []):
                relevance_score += 35

            item = EvidenceItem(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                evidence_type="finance_record",
                data_source=DataSourceType.FINANCE.value,
                source_record_id=record.id,
                title=f"财务记录: {record.category} - ¥{record.amount:,.2f}",
                description=(
                    f"报销单号: {record.record_id}\n"
                    f"员工: {record.employee_name or '未知'}\n"
                    f"标题: {record.title}\n"
                    f"类别: {record.category}\n"
                    f"状态: {record.status}\n"
                    f"供应商: {record.vendor_name or 'N/A'}\n"
                    f"标记: {', '.join(record.flags or [])}"
                ),
                related_employee_ids=list(emp_ids),
                event_timestamp=record.transaction_date,
                is_key_evidence=(relevance_score >= 50),
                relevance_score=min(100, relevance_score),
                chain_of_custody=[{
                    "action": "auto_collected",
                    "timestamp": datetime.utcnow().isoformat(),
                    "source": "evidence_engine",
                }],
                extra_metadata={
                    "amount": record.amount,
                    "invoice_count": record.invoice_count,
                    "payment_method": record.payment_method,
                },
            )
            items.append(item)

        if normal_records:
            total_amount = sum(r.amount for r in normal_records)
            by_category: Dict[str, float] = defaultdict(float)
            for r in normal_records:
                by_category[r.category or "其他"] += r.amount

            summary = (
                f"汇总期间: {start_time.strftime('%Y-%m-%d')} ~ {end_time.strftime('%Y-%m-%d')}\n"
                f"记录总数: {len(normal_records)}\n"
                f"总金额: ¥{total_amount:,.2f}\n\n"
                f"按类别统计:\n"
            )
            for cat, amt in sorted(by_category.items(), key=lambda x: -x[1]):
                summary += f"  {cat}: ¥{amt:,.2f}\n"

            item = EvidenceItem(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                evidence_type="finance_summary",
                data_source=DataSourceType.FINANCE.value,
                title=f"财务汇总分析 ({len(normal_records)}条, ¥{total_amount:,.2f})",
                description="正常财务记录的汇总分析",
                content_summary=summary,
                related_employee_ids=list(emp_ids),
                event_timestamp=start_time,
                is_key_evidence=False,
                relevance_score=25,
                chain_of_custody=[{
                    "action": "auto_collected",
                    "timestamp": datetime.utcnow().isoformat(),
                    "source": "evidence_engine",
                }],
            )
            items.append(item)

        return items

    async def _build_event_timeline(
        self,
        db: AsyncSession,
        ticket: InvestigationTicket,
        emp_ids: set,
        time_window: Tuple[datetime, datetime]
    ) -> List[EventTimeline]:
        entries: List[EventTimeline] = []
        start_time, end_time = time_window

        existing_result = await db.execute(
            select(EventTimeline).where(EventTimeline.ticket_id == ticket.id)
        )
        existing_entries = existing_result.scalars().all()
        existing_keys = {
            (e.timestamp.isoformat(), e.timeline_type, str(e.source_record_id))
            for e in existing_entries if e.source_record_id
        }

        data_queries = [
            (EmailRecord, EmailRecord.sent_at, "email", DataSourceType.EMAIL),
            (InstantMessageRecord, InstantMessageRecord.sent_at, "instant_message", DataSourceType.INSTANT_MESSAGE),
            (DoorAccessRecord, DoorAccessRecord.access_time, "door_access", DataSourceType.DOOR_ACCESS),
            (FinanceRecord, FinanceRecord.transaction_date, "finance", DataSourceType.FINANCE),
        ]

        for model, time_col, timeline_type, ds_type in data_queries:
            query = select(model).where(time_col.between(start_time, end_time))

            if hasattr(model, "sender_employee_id") and emp_ids:
                query = query.where(model.sender_employee_id.in_(list(emp_ids)))
            elif hasattr(model, "employee_id") and emp_ids:
                query = query.where(model.employee_id.in_(list(emp_ids)))

            query = query.order_by(time_col).limit(100)

            result = await db.execute(query)
            records = result.scalars().all()

            for record in records:
                ts = getattr(record, "sent_at", None) or getattr(record, "access_time", None) or getattr(record, "transaction_date", None)
                if not ts:
                    continue

                key = (ts.isoformat(), timeline_type, str(record.id))
                if key in existing_keys:
                    continue

                if hasattr(model, "sender_name"):
                    emp_name = record.sender_name
                elif hasattr(model, "employee_name"):
                    emp_name = record.employee_name
                else:
                    emp_name = None

                if hasattr(model, "subject"):
                    title = f"📧 {record.subject or '(无主题)'}"
                elif hasattr(model, "content"):
                    content = (record.content or "")[:100]
                    title = f"💬 {content}"
                elif hasattr(model, "location_name"):
                    title = f"🚪 {record.location_name} - {record.access_type}"
                elif hasattr(model, "category"):
                    title = f"💰 {record.category} - ¥{record.amount:,.2f}"
                else:
                    title = timeline_type

                desc = ""
                if hasattr(model, "location_name"):
                    desc = f"{record.location_name} ({record.location_type})"
                elif hasattr(model, "category"):
                    desc = f"{record.title or ''} - {record.vendor_name or ''}"
                elif hasattr(model, "sender_email"):
                    desc = f"From: {record.sender_name or record.sender_email}"

                entry = EventTimeline(
                    id=uuid.uuid4(),
                    ticket_id=ticket.id,
                    timestamp=ts,
                    timeline_type=timeline_type,
                    data_source=ds_type.value,
                    title=title,
                    description=desc,
                    source_record_id=record.id,
                    source_table=model.__tablename__,
                    employee_id=getattr(record, "sender_employee_id", None) or getattr(record, "employee_id", None),
                    employee_name=emp_name,
                    is_key_event=False,
                )
                entries.append(entry)

        return entries

    async def _finalize_package(
        self,
        db: AsyncSession,
        package: EvidencePackage,
        evidence_items: List[EvidenceItem],
        timeline_entries: List[EventTimeline],
    ):
        total_evidence = await self._get_total_evidence_count(db, package.ticket_id)
        package.evidence_count = total_evidence
        package.status = "ready"
        package.generated_at = datetime.utcnow()

        manifest = {
            "package_code": package.package_code,
            "generated_at": datetime.utcnow().isoformat(),
            "evidence_items": [
                {
                    "id": str(item.id),
                    "type": item.evidence_type,
                    "title": item.title,
                    "relevance_score": item.relevance_score,
                    "is_key": item.is_key_evidence,
                    "timestamp": item.event_timestamp.isoformat() if item.event_timestamp else None,
                }
                for item in evidence_items
            ],
            "timeline_entries": len(timeline_entries),
            "metadata": {
                "total_evidence_count": total_evidence,
                "key_evidence_count": sum(1 for i in evidence_items if i.is_key_evidence),
            },
        }

        manifest_path = os.path.join(
            settings.EVIDENCE_PACKAGE_DIR,
            f"{package.package_code}_manifest.json"
        )
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            with open(manifest_path, "rb") as f:
                manifest_hash = hashlib.sha256(f.read()).hexdigest()

            package.manifest = manifest
            package.file_path = manifest_path
            package.file_name = os.path.basename(manifest_path)
            package.file_hash = manifest_hash
            package.file_size = os.path.getsize(manifest_path)
        except Exception as e:
            self.logger.error("Failed to write evidence manifest", error=str(e))
            package.manifest = manifest

    async def _get_total_evidence_count(
        self, db: AsyncSession, ticket_id: uuid.UUID
    ) -> int:
        result = await db.execute(
            select(EvidenceItem).where(EvidenceItem.ticket_id == ticket_id)
        )
        return len(result.scalars().all())
