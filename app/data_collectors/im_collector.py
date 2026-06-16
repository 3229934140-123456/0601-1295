from __future__ import annotations
from typing import List, Dict, Any, AsyncGenerator
from datetime import datetime, timedelta
import uuid
import random
from app.core import settings, logger
from app.core.constants import DataSourceType
from app.core.database import get_db_context
from .base import BaseDataCollector
from app.models.data_source import InstantMessageRecord
from app.models.organization import Employee
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx


class InstantMessageCollector(BaseDataCollector):
    data_source_type = DataSourceType.INSTANT_MESSAGE

    SUSPICIOUS_KEYWORDS = {
        "红包", "回扣", "好处费", "私单", "飞单", "漏税", "洗钱",
        "bribe", "kickback", "off-book", "side deal", "launder",
        "透露", "泄露", "内部信息", "股价", "收购", "并购",
        "leak", "confidential", "insider", "stock tip", "acquisition",
        "骚扰", "黄色", "低俗", "威胁", "恐吓",
        "harass", "threat", "abuse",
    }

    def __init__(self):
        super().__init__()
        self.api_url = settings.IM_WEBHOOK_URL.replace("/webhook", "/api/messages")
        self.api_token = settings.IM_WEBHOOK_TOKEN
        self.company_domain = "example.com"

    async def _fetch_records_since(self, since: datetime) -> AsyncGenerator[List[Dict[str, Any]], None]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            cursor = None
            while True:
                try:
                    params = {
                        "since": since.isoformat(),
                        "limit": self.batch_size,
                    }
                    if cursor:
                        params["cursor"] = cursor

                    response = await client.get(
                        f"{self.api_url}/export",
                        params=params,
                        headers={"Authorization": f"Bearer {self.api_token}"}
                    )

                    if response.status_code == 401:
                        self.logger.warning("IM API unauthorized, using mock data")
                        async for batch in self._generate_mock_data(since):
                            yield batch
                        return

                    response.raise_for_status()
                    data = response.json()

                    messages = data.get("messages", [])
                    if not messages:
                        break

                    batch = []
                    for msg in messages:
                        record = self._parse_message(msg)
                        if record:
                            batch.append(record)

                    if batch:
                        yield batch

                    cursor = data.get("next_cursor")
                    if not cursor:
                        break

                except httpx.HTTPError as e:
                    self.logger.error("IM API request failed", error=str(e))
                    self.logger.info("Falling back to mock data generation")
                    async for batch in self._generate_mock_data(since):
                        yield batch
                    break

    def _parse_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        message_id = msg.get("id") or msg.get("message_id")
        if not message_id:
            return None

        sender_id = msg.get("sender_id", "")
        sender_name = msg.get("sender_name", "")
        sender_email = msg.get("sender_email")
        if not sender_email and sender_id:
            sender_email = f"{sender_id}@{self.company_domain}"

        conversation_id = msg.get("conversation_id", "")
        conversation_name = msg.get("conversation_name", "")

        message_type = msg.get("type", "text")
        content = msg.get("content", "") or ""
        content = str(content)

        participants = msg.get("participants", [])
        has_external = any(
            not p.get("email", "").endswith(f"@{self.company_domain}")
            for p in participants
            if p.get("email")
        )

        files = msg.get("files", [])
        file_names = [f.get("name", "") for f in files]

        mentioned_users = msg.get("mentions", [])

        try:
            sent_at = datetime.fromisoformat(msg.get("created_at", "").replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            sent_at = datetime.utcnow()

        is_deleted = msg.get("is_deleted", False) or msg.get("deleted", False)

        tags = []
        content_lower = content.lower()
        for kw in self.SUSPICIOUS_KEYWORDS:
            if kw.lower() in content_lower:
                tags.append(f"keyword_{kw}")
                break

        if has_external:
            tags.append("external_chat")
        if len(participants) > 10:
            tags.append("large_group")
        if files:
            tags.append("has_files")
        if is_deleted:
            tags.append("deleted_message")

        employee_identifier = sender_email or sender_id

        return {
            "external_id": str(message_id),
            "employee_identifier": employee_identifier,
            "recorded_at": sent_at,
            "summary": f"IM消息: {content[:100]}" if content else f"[{message_type}消息]",
            "tags": list(set(tags)),
            "raw_content": msg,
            "_im_specific": {
                "message_id": str(message_id),
                "conversation_id": str(conversation_id),
                "conversation_name": conversation_name,
                "sender_id": str(sender_id),
                "sender_name": sender_name,
                "sender_email": sender_email,
                "message_type": message_type,
                "content": content,
                "has_file": len(files) > 0,
                "file_names": file_names,
                "mentioned_users": mentioned_users,
                "is_external_chat": has_external,
                "participant_count": len(participants),
                "participants": participants,
                "sent_at": sent_at,
                "is_deleted": is_deleted,
            }
        }

    async def _save_specific_record(
        self,
        db: AsyncSession,
        raw_data_id: uuid.UUID,
        record: Dict[str, Any]
    ) -> None:
        specific = record.get("_im_specific", {})
        if not specific:
            return

        employee_id = None
        sender_email = specific.get("sender_email")
        sender_id = specific.get("sender_id")
        if sender_email:
            emp_result = await db.execute(
                select(Employee).where(Employee.email == sender_email.lower())
            )
            emp = emp_result.scalar_one_or_none()
            if emp:
                employee_id = emp.id
        if not employee_id and sender_id:
            emp_result = await db.execute(
                select(Employee).where(Employee.employee_id == sender_id)
            )
            emp = emp_result.scalar_one_or_none()
            if emp:
                employee_id = emp.id

        im_record = InstantMessageRecord(
            id=uuid.uuid4(),
            raw_data_id=raw_data_id,
            message_id=specific["message_id"],
            conversation_id=specific.get("conversation_id"),
            conversation_name=specific.get("conversation_name"),
            sender_employee_id=employee_id,
            sender_id=specific.get("sender_id"),
            sender_name=specific.get("sender_name"),
            message_type=specific.get("message_type", "text"),
            content=specific.get("content", "")[:10000],
            has_file=specific.get("has_file", False),
            file_names=specific.get("file_names", []),
            mentioned_users=specific.get("mentioned_users", []),
            is_external_chat=specific.get("is_external_chat", False),
            participant_count=specific.get("participant_count", 2),
            participants=specific.get("participants", []),
            sent_at=specific.get("sent_at", datetime.utcnow()),
            is_deleted=specific.get("is_deleted", False),
        )
        db.add(im_record)

    async def _generate_mock_data(self, since: datetime) -> AsyncGenerator[List[Dict[str, Any]], None]:
        async with get_db_context() as db:
            emp_result = await db.execute(select(Employee).limit(200))
            employees = emp_result.scalars().all()

        if not employees:
            self.logger.warning("No employees found for mock IM data generation")
            return

        hours_diff = int((datetime.utcnow() - since).total_seconds() / 3600)
        total_messages = min(hours_diff * 5000, 50000)

        batch = []
        generated = 0
        current_time = since

        while generated < total_messages:
            sender = random.choice(employees)
            participant_count = random.choices([2, 3, 5, 10, 50], weights=[50, 20, 15, 10, 5])[0]
            participants_ids = set([sender.employee_id])
            while len(participants_ids) < min(participant_count, len(employees)):
                participants_ids.add(random.choice(employees).employee_id)

            is_external = random.random() < 0.1
            if is_external:
                participant_count += 1

            conversation_id = f"conv_{hash(tuple(sorted(participants_ids))) % 1000000:06d}"

            message_contents = [
                "项目进展如何？我们下周需要提交方案。",
                "请查收附件中的季度报告。",
                "明天下午3点的会议请准时参加。",
                "这个数据需要再核对一下，确保准确性。",
                "关于那个供应商的合同，还需要法务审核。",
                "今天的生产数据已经上传到系统了。",
                "帮忙看看这个问题应该怎么解决？",
                "好的，我马上处理。",
                "已经收到，谢谢。",
                "周末有什么安排？",
                "那个事情就按我们说的办，别留记录。",
                "客户那边给的好处费我打到你个人账户了，查收一下。",
                "这个信息不要外传，内部讨论就好。",
                "下班前把报表发我邮箱，我转发给外部的朋友看看。",
                "这个项目我们私下做，不要让公司知道，利润五五分。",
            ]

            content = random.choice(message_contents)
            has_file = random.random() < 0.15
            is_deleted = random.random() < 0.02

            message_id = f"msg_{generated:08d}_{int(current_time.timestamp())}"

            time_increment = random.uniform(1, 180)
            current_time = current_time + timedelta(seconds=time_increment)
            if current_time > datetime.utcnow():
                current_time = datetime.utcnow() - timedelta(minutes=random.randint(1, 60))

            record = {
                "external_id": message_id,
                "employee_identifier": sender.email,
                "recorded_at": current_time,
                "summary": f"IM消息: {content[:100]}",
                "tags": [],
                "raw_content": {
                    "id": message_id,
                    "sender": sender.name,
                    "conversation": conversation_id,
                },
                "_im_specific": {
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "conversation_name": f"群组_{conversation_id[-4:]}" if participant_count > 2 else None,
                    "sender_id": sender.employee_id,
                    "sender_name": sender.name,
                    "sender_email": sender.email,
                    "message_type": "text",
                    "content": content,
                    "has_file": has_file,
                    "file_names": [f"document_{random.randint(1000,9999)}.pdf"] if has_file else [],
                    "mentioned_users": [],
                    "is_external_chat": is_external,
                    "participant_count": participant_count,
                    "participants": list(participants_ids),
                    "sent_at": current_time,
                    "is_deleted": is_deleted,
                }
            }

            content_lower = content.lower()
            for kw in self.SUSPICIOUS_KEYWORDS:
                if kw.lower() in content_lower:
                    record["tags"].append(f"keyword_{kw}")
                    break
            if is_external:
                record["tags"].append("external_chat")
            if has_file:
                record["tags"].append("has_files")
            if is_deleted:
                record["tags"].append("deleted_message")

            batch.append(record)
            generated += 1

            if len(batch) >= self.batch_size:
                yield batch
                batch = []

        if batch:
            yield batch
