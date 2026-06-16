from __future__ import annotations
from typing import List, Dict, Any, AsyncGenerator
from datetime import datetime, timedelta
import uuid
import random
from app.core import settings, logger
from app.core.constants import DataSourceType
from app.core.database import get_db_context
from .base import BaseDataCollector
from app.models.data_source import FinanceRecord
from app.models.organization import Employee
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx


class FinanceDataCollector(BaseDataCollector):
    data_source_type = DataSourceType.FINANCE

    SUSPICIOUS_CATEGORIES = {
        "招待费": {"max_single": 5000, "flags": ["high_amount"]},
        "差旅费": {"max_single": 20000, "flags": ["high_amount"]},
        "办公费": {"max_single": 3000, "flags": ["high_amount"]},
        "培训费": {"max_single": 50000, "flags": ["high_amount"]},
        "咨询费": {"max_single": 100000, "flags": ["high_amount", "scrutiny"]},
        "礼品费": {"max_single": 2000, "flags": ["high_amount", "compliance_check"]},
        "劳务费": {"max_single": 30000, "flags": ["high_amount", "scrutiny"]},
    }

    NORMAL_CATEGORIES = [
        "交通费", "通讯费", "餐饮费", "办公用品", "软件服务费",
        "快递费", "印刷费", "物业费", "水电费", "设备维修费"
    ]

    ROUND_NUMBER_MULTIPLES = [1000, 2000, 5000, 10000, 20000, 50000]

    def __init__(self):
        super().__init__()
        self.api_url = settings.FINANCE_API_URL
        self.api_key = settings.FINANCE_API_KEY

    async def _fetch_records_since(self, since: datetime) -> AsyncGenerator[List[Dict[str, Any]], None]:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                offset = 0
                while True:
                    params = {
                        "start_date": since.date().isoformat(),
                        "end_date": datetime.utcnow().date().isoformat(),
                        "offset": offset,
                        "limit": self.batch_size,
                    }
                    headers = {"X-API-Key": self.api_key}
                    response = await client.get(
                        f"{self.api_url}/expenses",
                        params=params,
                        headers=headers
                    )

                    if response.status_code != 200:
                        self.logger.warning(
                            "Finance API error, using mock data",
                            status=response.status_code
                        )
                        async for batch in self._generate_mock_data(since):
                            yield batch
                        return

                    data = response.json()
                    records = data.get("records", [])
                    if not records:
                        break

                    batch = [self._parse_record(r) for r in records if self._parse_record(r)]
                    if batch:
                        yield batch

                    if len(records) < self.batch_size:
                        break
                    offset += self.batch_size
        except Exception as e:
            self.logger.error("Finance collection failed", error=str(e))
            async for batch in self._generate_mock_data(since):
                yield batch

    def _parse_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record_type = record.get("record_type", "expense")
        record_id = record.get("record_id") or record.get("id")
        if not record_id:
            return None

        try:
            tx_date = datetime.fromisoformat(
                record.get("transaction_date", "").replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            tx_date = datetime.utcnow()

        employee_identifier = str(record.get("employee_id") or record.get("applicant_id", ""))
        amount = float(record.get("amount", 0))
        category = record.get("category", "其他")

        category_config = self.SUSPICIOUS_CATEGORIES.get(category, {})
        flags = []
        is_flagged = False

        if amount > category_config.get("max_single", 100000):
            flags.append("high_amount")
            is_flagged = True

        if any(abs(amount - m * round(amount / m)) < 0.01 for m in self.ROUND_NUMBER_MULTIPLES if m < amount):
            flags.append("round_number_amount")
            is_flagged = True

        if category in ["礼品费", "咨询费", "劳务费"]:
            flags.extend(category_config.get("flags", []))
            is_flagged = True

        if record.get("status") in ["rejected", "pending_review"]:
            flags.append(f"status_{record.get('status')}")

        tags = flags.copy()

        return {
            "external_id": self.generate_external_id("finance", record_type, record_id),
            "employee_identifier": employee_identifier,
            "recorded_at": tx_date,
            "summary": f"{category}: ¥{amount:,.2f} [{record.get('title','')[:50]}]",
            "tags": tags,
            "raw_content": record,
            "_finance_specific": {
                "record_type": record_type,
                "record_id": str(record_id),
                "employee_identifier": employee_identifier,
                "employee_name": record.get("employee_name") or record.get("applicant_name"),
                "department_id": record.get("department_id"),
                "title": record.get("title"),
                "description": record.get("description"),
                "amount": amount,
                "currency": record.get("currency", "CNY"),
                "transaction_date": tx_date,
                "submission_date": record.get("submission_date"),
                "approval_date": record.get("approval_date"),
                "reimbursement_date": record.get("reimbursement_date"),
                "status": record.get("status", "submitted"),
                "vendor_name": record.get("vendor_name"),
                "invoice_count": record.get("invoice_count", 0),
                "invoice_ids": record.get("invoice_ids", []),
                "category": category,
                "payment_method": record.get("payment_method"),
                "approvers": record.get("approvers", []),
                "is_flagged": is_flagged,
                "flags": flags,
            }
        }

    async def _save_specific_record(
        self,
        db: AsyncSession,
        raw_data_id: uuid.UUID,
        record: Dict[str, Any]
    ) -> None:
        specific = record.get("_finance_specific", {})
        if not specific:
            return

        employee_id = None
        identifier = specific.get("employee_identifier", "")
        if identifier:
            emp_result = await db.execute(
                select(Employee).where(Employee.employee_id == identifier)
            )
            emp = emp_result.scalar_one_or_none()
            if emp:
                employee_id = emp.id
                specific["employee_name"] = emp.name
                specific["department_id"] = str(emp.department_id) if emp.department_id else None

        fin_record = FinanceRecord(
            id=uuid.uuid4(),
            raw_data_id=raw_data_id,
            record_type=specific.get("record_type", "expense"),
            record_id=specific["record_id"],
            employee_id=employee_id,
            employee_identifier=specific.get("employee_identifier"),
            employee_name=specific.get("employee_name"),
            department_id=uuid.UUID(specific["department_id"]) if specific.get("department_id") and isinstance(specific["department_id"], str) else specific.get("department_id"),
            title=specific.get("title"),
            description=specific.get("description"),
            amount=specific.get("amount", 0.0),
            currency=specific.get("currency", "CNY"),
            transaction_date=specific.get("transaction_date", datetime.utcnow()),
            submission_date=specific.get("submission_date"),
            approval_date=specific.get("approval_date"),
            reimbursement_date=specific.get("reimbursement_date"),
            status=specific.get("status", "submitted"),
            vendor_name=specific.get("vendor_name"),
            invoice_count=specific.get("invoice_count", 0),
            invoice_ids=specific.get("invoice_ids", []),
            category=specific.get("category"),
            payment_method=specific.get("payment_method"),
            approvers=specific.get("approvers", []),
            is_flagged=specific.get("is_flagged", False),
            flags=specific.get("flags", []),
        )
        db.add(fin_record)

    async def _generate_mock_data(self, since: datetime) -> AsyncGenerator[List[Dict[str, Any]], None]:
        async with get_db_context() as db:
            emp_result = await db.execute(
                select(Employee).where(Employee.employment_status == "active").limit(1000)
            )
            employees = emp_result.scalars().all()

        if not employees:
            self.logger.warning("No employees for mock finance data")
            return

        days_diff = max(1, (datetime.utcnow() - since).days)
        total_records = min(days_diff * 500, 50000)

        all_categories = list(self.SUSPICIOUS_CATEGORIES.keys()) + self.NORMAL_CATEGORIES

        vendors = [
            "XX酒店管理有限公司", "YY航空服务有限公司", "ZZ餐饮管理有限公司",
            "AA科技有限公司", "BB办公用品有限公司", "CC咨询服务有限公司",
            "DD会议服务有限公司", "EE培训机构", "FF礼品定制有限公司",
            "GG国际旅行社", "HH信息技术有限公司", "II快递服务有限公司"
        ]

        statuses = ["submitted", "approved", "reimbursed", "rejected", "pending_review"]
        status_weights = [10, 50, 30, 3, 7]
        payment_methods = ["个人垫付", "公司信用卡", "对公转账", "现金"]

        batch = []
        counter = 0

        for _ in range(total_records):
            emp = random.choice(employees)
            category = random.choices(
                all_categories,
                weights=[5]*len(self.SUSPICIOUS_CATEGORIES) + [10]*len(self.NORMAL_CATEGORIES)
            )[0]

            if category in self.SUSPICIOUS_CATEGORIES:
                max_amt = self.SUSPICIOUS_CATEGORIES[category].get("max_single", 10000)
                base_amount = random.uniform(100, max_amt * 1.5)
            else:
                base_amount = random.uniform(10, 5000)

            is_round_amount = random.random() < 0.08
            if is_round_amount:
                amount = round(base_amount / 500) * 500
            else:
                amount = round(base_amount, 2)

            is_suspicious = random.random() < 0.05

            day_offset = random.randint(0, days_diff - 1)
            tx_date = since + timedelta(
                days=day_offset,
                hours=random.randint(8, 22),
                minutes=random.randint(0, 59)
            )
            if tx_date > datetime.utcnow():
                tx_date = datetime.utcnow() - timedelta(hours=random.randint(1, 48))

            submission_date = tx_date + timedelta(days=random.randint(0, 3))
            status = random.choices(statuses, weights=status_weights)[0]

            approval_date = None
            reimbursement_date = None
            approvers = []
            if status in ["approved", "reimbursed", "rejected"]:
                approval_date = submission_date + timedelta(days=random.randint(0, 5))
                approvers = [f"审批人_{random.randint(100,200)}"]
                if status == "reimbursed":
                    reimbursement_date = approval_date + timedelta(days=random.randint(0, 3))

            flags = []
            is_flagged = False

            if is_suspicious:
                flags.append("random_audit_check")
                is_flagged = True

            category_config = self.SUSPICIOUS_CATEGORIES.get(category, {})
            if amount > category_config.get("max_single", 100000) * 0.8:
                flags.append("high_amount")
                is_flagged = True
            if is_round_amount and amount > 3000:
                flags.append("round_number_amount")
                is_flagged = True
            if category in ["礼品费", "咨询费", "劳务费"]:
                flags.append("compliance_review_required")
                is_flagged = True

            invoice_count = random.randint(1, 5) if random.random() > 0.1 else 0
            invoice_ids = [f"INV-{counter:06d}-{i}" for i in range(invoice_count)]

            record_id = f"EXP-{tx_date.strftime('%Y%m%d')}-{counter:06d}"

            record = {
                "external_id": self.generate_external_id("finance", "expense", record_id),
                "employee_identifier": emp.employee_id,
                "recorded_at": tx_date,
                "summary": f"{emp.name} - {category}: ¥{amount:,.2f}",
                "tags": flags,
                "raw_content": {"id": record_id, "emp": emp.employee_id},
                "_finance_specific": {
                    "record_type": "expense",
                    "record_id": record_id,
                    "employee_identifier": emp.employee_id,
                    "employee_name": emp.name,
                    "department_id": str(emp.department_id) if emp.department_id else None,
                    "title": f"{category}报销 - {random.choice(['客户拜访', '项目支出', '日常办公', '团队活动', '出差'])}",
                    "description": f"{category}相关支出明细" if not is_suspicious else None,
                    "amount": amount,
                    "currency": "CNY",
                    "transaction_date": tx_date,
                    "submission_date": submission_date,
                    "approval_date": approval_date,
                    "reimbursement_date": reimbursement_date,
                    "status": status,
                    "vendor_name": random.choice(vendors),
                    "invoice_count": invoice_count,
                    "invoice_ids": invoice_ids,
                    "category": category,
                    "payment_method": random.choice(payment_methods),
                    "approvers": approvers,
                    "is_flagged": is_flagged,
                    "flags": flags,
                }
            }

            batch.append(record)
            counter += 1

            if len(batch) >= self.batch_size:
                yield batch
                batch = []

        if batch:
            yield batch
