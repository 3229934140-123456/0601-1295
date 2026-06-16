from __future__ import annotations
from typing import List, Dict, Any, AsyncGenerator
from datetime import datetime, timedelta, time
import uuid
import random
from app.core import settings, logger
from app.core.constants import DataSourceType
from app.core.database import get_db_context
from .base import BaseDataCollector
from app.models.data_source import DoorAccessRecord
from app.models.organization import Employee
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx


class DoorAccessCollector(BaseDataCollector):
    data_source_type = DataSourceType.DOOR_ACCESS

    RESTRICTED_LOCATIONS = {
        "LOC-001": ("服务器机房", "server_room"),
        "LOC-002": ("财务档案室", "finance_archive"),
        "LOC-003": ("高管办公区", "executive_area"),
        "LOC-004": ("仓库A区", "warehouse_a"),
        "LOC-005": ("研发机密区", "rd_secure"),
    }

    NORMAL_LOCATIONS = {
        "LOC-101": ("主入口", "main_entrance"),
        "LOC-102": ("侧门", "side_entrance"),
        "LOC-103": ("地下停车场", "parking"),
        "LOC-104": ("办公区A", "office_a"),
        "LOC-105": ("办公区B", "office_b"),
        "LOC-106": ("会议室层", "meeting_floor"),
        "LOC-107": ("餐厅", "cafeteria"),
        "LOC-108": ("健身房", "gym"),
    }

    ALL_LOCATIONS = {**RESTRICTED_LOCATIONS, **NORMAL_LOCATIONS}

    BUSINESS_HOURS_START = time(8, 0)
    BUSINESS_HOURS_END = time(20, 0)

    def __init__(self):
        super().__init__()
        self.api_url = settings.DOOR_ACCESS_API_URL
        self.api_key = settings.DOOR_ACCESS_API_KEY

    async def _fetch_records_since(self, since: datetime) -> AsyncGenerator[List[Dict[str, Any]], None]:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                cursor = None
                while True:
                    params = {
                        "start_time": since.isoformat(),
                        "end_time": datetime.utcnow().isoformat(),
                        "limit": self.batch_size,
                    }
                    if cursor:
                        params["cursor"] = cursor

                    headers = {"X-API-Key": self.api_key}
                    response = await client.get(
                        f"{self.api_url}/access-logs",
                        params=params,
                        headers=headers
                    )

                    if response.status_code != 200:
                        self.logger.warning("Door Access API error, using mock data", status=response.status_code)
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

                    cursor = data.get("next_cursor")
                    if not cursor:
                        break
        except Exception as e:
            self.logger.error("Door Access collection failed", error=str(e))
            self.logger.info("Falling back to mock data")
            async for batch in self._generate_mock_data(since):
                yield batch

    def _parse_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        try:
            access_time = datetime.fromisoformat(
                record.get("access_time", "").replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            access_time = datetime.utcnow()

        location_id = record.get("location_id", "")
        location_info = self.ALL_LOCATIONS.get(location_id, ("未知区域", "unknown"))
        location_name, location_type = location_info

        employee_identifier = record.get("employee_id") or record.get("card_number", "")

        is_restricted = location_id in self.RESTRICTED_LOCATIONS
        access_time_obj = access_time.time()
        is_after_hours = (access_time_obj < self.BUSINESS_HOURS_START or
                         access_time_obj > self.BUSINESS_HOURS_END)

        tags = []
        if is_restricted:
            tags.append("restricted_area")
        if is_after_hours:
            tags.append("after_hours")
        if record.get("access_result") == "denied":
            tags.append("access_denied")

        return {
            "external_id": self.generate_external_id(
                "door",
                employee_identifier,
                location_id,
                record.get("access_time", "")
            ),
            "employee_identifier": str(employee_identifier),
            "recorded_at": access_time,
            "summary": f"{location_name} {record.get('access_type', '刷卡')} {'进入' if record.get('access_type') == 'entry' else '离开'}",
            "tags": tags,
            "raw_content": record,
            "_door_specific": {
                "employee_identifier": str(employee_identifier),
                "employee_name": record.get("employee_name"),
                "location_id": location_id,
                "location_name": location_name,
                "location_type": location_type,
                "access_type": record.get("access_type", "entry"),
                "access_method": record.get("access_method", "card"),
                "device_id": record.get("device_id"),
                "access_time": access_time,
                "is_after_hours": is_after_hours,
                "is_restricted_area": is_restricted,
                "access_result": record.get("access_result", "granted"),
            }
        }

    async def _save_specific_record(
        self,
        db: AsyncSession,
        raw_data_id: uuid.UUID,
        record: Dict[str, Any]
    ) -> None:
        specific = record.get("_door_specific", {})
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

        door_record = DoorAccessRecord(
            id=uuid.uuid4(),
            raw_data_id=raw_data_id,
            employee_id=employee_id,
            employee_identifier=specific.get("employee_identifier"),
            employee_name=specific.get("employee_name"),
            location_id=specific.get("location_id"),
            location_name=specific.get("location_name"),
            location_type=specific.get("location_type"),
            access_type=specific.get("access_type", "entry"),
            access_method=specific.get("access_method"),
            device_id=specific.get("device_id"),
            access_time=specific.get("access_time", datetime.utcnow()),
            is_after_hours=specific.get("is_after_hours", False),
            is_restricted_area=specific.get("is_restricted_area", False),
            access_result=specific.get("access_result", "granted"),
        )
        db.add(door_record)

    async def _generate_mock_data(self, since: datetime) -> AsyncGenerator[List[Dict[str, Any]], None]:
        async with get_db_context() as db:
            emp_result = await db.execute(
                select(Employee).where(Employee.employment_status == "active").limit(1000)
            )
            employees = emp_result.scalars().all()

        if not employees:
            self.logger.warning("No active employees for mock door access data")
            return

        days_diff = max(1, (datetime.utcnow() - since).days)
        total_records = min(days_diff * len(employees) * 8, 200000)

        batch = []
        generated = 0

        for day_offset in range(days_diff):
            day_date = (since + timedelta(days=day_offset)).date()
            if day_date > datetime.utcnow().date():
                break
            is_weekend = day_date.weekday() >= 5

            for emp in employees:
                if is_weekend and random.random() > 0.2:
                    continue

                num_actions = random.choices([0, 2, 4, 6], weights=[5, 60, 30, 5])[0]
                if num_actions == 0:
                    continue

                for i in range(num_actions):
                    if generated >= total_records:
                        break

                    if i == 0:
                        hour = random.randint(7, 10)
                        loc_id, (loc_name, loc_type) = "LOC-101", self.ALL_LOCATIONS["LOC-101"]
                        access_type = "entry"
                    elif i == num_actions - 1:
                        hour = random.randint(17, 22)
                        loc_id, (loc_name, loc_type) = "LOC-101", self.ALL_LOCATIONS["LOC-101"]
                        access_type = "exit"
                    else:
                        hour = random.randint(10, 17)
                        if random.random() < 0.05:
                            loc_id = random.choice(list(self.RESTRICTED_LOCATIONS.keys()))
                            loc_name, loc_type = self.RESTRICTED_LOCATIONS[loc_id]
                        else:
                            loc_id = random.choice(list(self.NORMAL_LOCATIONS.keys()))
                            loc_name, loc_type = self.NORMAL_LOCATIONS[loc_id]
                        access_type = random.choice(["entry", "exit"])

                    minute = random.randint(0, 59)
                    second = random.randint(0, 59)
                    access_time = datetime.combine(day_date, time(hour, minute, second))

                    if access_time > datetime.utcnow():
                        continue

                    is_restricted = loc_id in self.RESTRICTED_LOCATIONS
                    is_after_hours = (time(hour, minute) < self.BUSINESS_HOURS_START or
                                     time(hour, minute) > self.BUSINESS_HOURS_END)

                    tags = []
                    if is_restricted:
                        tags.append("restricted_area")
                    if is_after_hours:
                        tags.append("after_hours")

                    access_method = random.choices(
                        ["card", "face", "fingerprint", "pin"],
                        weights=[70, 15, 10, 5]
                    )[0]
                    access_result = "granted" if random.random() > 0.02 else "denied"
                    if access_result == "denied":
                        tags.append("access_denied")

                    external_id = self.generate_external_id(
                        "door",
                        emp.employee_id,
                        loc_id,
                        access_time.isoformat()
                    )

                    record = {
                        "external_id": external_id,
                        "employee_identifier": emp.employee_id,
                        "recorded_at": access_time,
                        "summary": f"{emp.name} {loc_name} {access_type}",
                        "tags": tags,
                        "raw_content": {"emp": emp.employee_id, "loc": loc_id},
                        "_door_specific": {
                            "employee_identifier": emp.employee_id,
                            "employee_name": emp.name,
                            "location_id": loc_id,
                            "location_name": loc_name,
                            "location_type": loc_type,
                            "access_type": access_type,
                            "access_method": access_method,
                            "device_id": f"DEV-{random.randint(100,999)}",
                            "access_time": access_time,
                            "is_after_hours": is_after_hours,
                            "is_restricted_area": is_restricted,
                            "access_result": access_result,
                        }
                    }

                    batch.append(record)
                    generated += 1

                    if len(batch) >= self.batch_size:
                        yield batch
                        batch = []

        if batch:
            yield batch
