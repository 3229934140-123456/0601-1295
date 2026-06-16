from __future__ import annotations
from typing import List, Dict, Any, AsyncGenerator
from datetime import datetime
import uuid
import email
from email import policy
from email.message import EmailMessage
import imaplib
import re
import asyncio
from app.core import settings, logger
from app.core.constants import DataSourceType
from app.core.database import get_db_context
from .base import BaseDataCollector
from app.models.data_source import EmailRecord
from app.models.organization import Employee
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential


class EmailDataCollector(BaseDataCollector):
    data_source_type = DataSourceType.EMAIL

    EXTERNAL_DOMAINS = {"gmail.com", "outlook.com", "yahoo.com", "hotmail.com"}
    SENSITIVE_KEYWORDS = ["机密", "secret", "confidential", "密码", "password", "密钥", "工资", "薪资"]
    LARGE_ATTACHMENT_THRESHOLD = 5 * 1024 * 1024

    def __init__(self):
        super().__init__()
        self.company_domain = "example.com"
        self._imap_conn = None

    async def _connect_imap(self):
        loop = asyncio.get_event_loop()
        try:
            self._imap_conn = await loop.run_in_executor(
                None, self._connect_sync
            )
        except Exception as e:
            self.logger.error("IMAP connection failed", error=str(e))
            raise

    def _connect_sync(self):
        mail = imaplib.IMAP4_SSL(settings.MAIL_SERVER, settings.MAIL_PORT)
        mail.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
        mail.select("INBOX")
        return mail

    async def _disconnect_imap(self):
        if self._imap_conn:
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, self._imap_conn.close)
                await loop.run_in_executor(None, self._imap_conn.logout)
            except Exception:
                pass
            self._imap_conn = None

    async def _fetch_records_since(self, since: datetime) -> AsyncGenerator[List[Dict[str, Any]], None]:
        try:
            await self._connect_imap()
            date_str = since.strftime("%d-%b-%Y")
            loop = asyncio.get_event_loop()

            _, search_data = await loop.run_in_executor(
                None,
                self._imap_conn.search,
                None,
                f'(SINCE "{date_str}")'
            )

            email_ids = search_data[0].split()
            batch = []

            for idx, e_id in enumerate(email_ids):
                try:
                    _, msg_data = await loop.run_in_executor(
                        None,
                        self._imap_conn.fetch,
                        e_id,
                        "(RFC822)"
                    )
                    msg = email.message_from_bytes(
                        msg_data[0][1],
                        policy=policy.default
                    )
                    record = self._parse_email_msg(msg)
                    if record:
                        batch.append(record)

                    if len(batch) >= self.batch_size:
                        yield batch
                        batch = []
                except Exception as e:
                    self.logger.error(
                        "Failed to parse email",
                        email_id=e_id.decode() if isinstance(e_id, bytes) else e_id,
                        error=str(e)
                    )
                    continue

            if batch:
                yield batch
        finally:
            await self._disconnect_imap()

    def _parse_email_msg(self, msg: EmailMessage) -> Dict[str, Any]:
        message_id = msg.get("Message-ID", "")
        if not message_id:
            message_id = self.generate_external_id(
                "email",
                msg.get("Date", ""),
                msg.get("From", ""),
                msg.get("Subject", "")
            )
        else:
            message_id = message_id.strip("<>")

        sender_email = msg.get("From", "")
        sender_match = re.search(r"[\w\.-]+@[\w\.-]+", sender_email)
        sender_email = sender_match.group(0) if sender_match else sender_email
        sender_name = msg.get("From", "").split("<")[0].strip('" ').strip()

        recipients_to = self._parse_addresses(msg.get("To", ""))
        recipients_cc = self._parse_addresses(msg.get("Cc", ""))
        recipients_bcc = self._parse_addresses(msg.get("Bcc", ""))

        all_recipients = recipients_to + recipients_cc + recipients_bcc
        external_count = sum(
            1 for addr in all_recipients
            if not addr.endswith(f"@{self.company_domain}")
        )

        subject = msg.get("Subject", "") or ""
        body_preview = ""
        body_full = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type in ("text/plain", "text/html"):
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        content = payload.decode(charset, errors="ignore")
                        if content_type == "text/plain":
                            body_preview = content[:2000]
                            body_full = content
                    except Exception:
                        pass
        else:
            try:
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or "utf-8"
                content = payload.decode(charset, errors="ignore")
                body_preview = content[:2000]
                body_full = content
            except Exception:
                pass

        attachments = []
        attachment_sizes = []
        has_large_attachment = False
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    filename = part.get_filename() or "unknown"
                    size = len(part.get_payload(decode=True) or b"")
                    attachments.append(filename)
                    attachment_sizes.append(size)
                    if size > self.LARGE_ATTACHMENT_THRESHOLD:
                        has_large_attachment = True

        sensitivity_level = None
        for kw in self.SENSITIVE_KEYWORDS:
            if kw.lower() in subject.lower() or kw.lower() in body_preview.lower():
                sensitivity_level = "high"
                break

        date_str = msg.get("Date", "")
        try:
            sent_at = email.utils.parsedate_to_datetime(date_str).replace(tzinfo=None) if date_str else datetime.utcnow()
        except Exception:
            sent_at = datetime.utcnow()

        is_external = not sender_email.endswith(f"@{self.company_domain}") or external_count > 0

        tags = []
        if external_count > 0:
            tags.append("external_recipients")
        if attachments:
            tags.append("has_attachment")
        if has_large_attachment:
            tags.append("large_attachment")
        if sensitivity_level:
            tags.append("sensitive_content")

        return {
            "external_id": message_id,
            "employee_identifier": sender_email,
            "recorded_at": sent_at,
            "summary": f"邮件: {subject[:100]}" if subject else "无主题邮件",
            "tags": tags,
            "raw_content": {
                "message_id": message_id,
                "sender_email": sender_email,
                "sender_name": sender_name,
                "to": recipients_to,
                "cc": recipients_cc,
                "bcc": recipients_bcc,
                "subject": subject,
                "has_attachment": len(attachments) > 0,
                "attachment_count": len(attachments),
                "sensitivity_level": sensitivity_level,
                "is_external": is_external,
                "external_recipient_count": external_count,
            },
            "_email_specific": {
                "message_id": message_id,
                "sender_email": sender_email,
                "sender_name": sender_name,
                "recipient_to": recipients_to,
                "recipient_cc": recipients_cc,
                "recipient_bcc": recipients_bcc,
                "external_recipient_count": external_count,
                "subject": subject,
                "body_preview": body_preview,
                "body_full": body_full,
                "has_attachment": len(attachments) > 0,
                "attachment_count": len(attachments),
                "attachment_names": attachments,
                "attachment_sizes": attachment_sizes,
                "sent_at": sent_at,
                "sensitivity_level": sensitivity_level,
                "is_external": is_external,
            }
        }

    @staticmethod
    def _parse_addresses(header_value: str) -> List[str]:
        if not header_value:
            return []
        result = []
        for addr in header_value.split(","):
            match = re.search(r"[\w\.-]+@[\w\.-]+", addr.strip())
            if match:
                result.append(match.group(0).lower())
        return result

    async def _save_specific_record(
        self,
        db: AsyncSession,
        raw_data_id: uuid.UUID,
        record: Dict[str, Any]
    ) -> None:
        specific = record.get("_email_specific", {})
        if not specific:
            return

        employee_id = None
        if specific.get("sender_email"):
            emp_result = await db.execute(
                select(Employee).where(
                    Employee.email == specific["sender_email"].lower()
                )
            )
            emp = emp_result.scalar_one_or_none()
            if emp:
                employee_id = emp.id

        email_record = EmailRecord(
            id=uuid.uuid4(),
            raw_data_id=raw_data_id,
            message_id=specific["message_id"],
            sender_employee_id=employee_id,
            sender_email=specific.get("sender_email", "").lower(),
            sender_name=specific.get("sender_name"),
            recipient_to=specific.get("recipient_to", []),
            recipient_cc=specific.get("recipient_cc", []),
            recipient_bcc=specific.get("recipient_bcc", []),
            external_recipient_count=specific.get("external_recipient_count", 0),
            subject=specific.get("subject"),
            body_preview=specific.get("body_preview"),
            has_attachment=specific.get("has_attachment", False),
            attachment_count=specific.get("attachment_count", 0),
            attachment_names=specific.get("attachment_names", []),
            attachment_sizes=specific.get("attachment_sizes", []),
            sent_at=specific.get("sent_at", datetime.utcnow()),
            sensitivity_level=specific.get("sensitivity_level"),
            is_external=specific.get("is_external", False),
        )
        db.add(email_record)
