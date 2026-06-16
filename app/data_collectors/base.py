from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncGenerator
from datetime import datetime, timedelta
import hashlib
import asyncio
import uuid
from tenacity import retry, stop_after_attempt, wait_exponential
from app.core import logger, settings
from app.core.database import get_db_context
from app.core.constants import DataSourceType
from app.models.data_source import RawDataRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class BaseDataCollector(ABC):
    data_source_type: DataSourceType

    def __init__(self):
        self.batch_size = settings.DATA_COLLECTION_BATCH_SIZE
        self.logger = logger.bind(collector=self.__class__.__name__, source=self.data_source_type.value)

    async def collect_since(self, since: datetime) -> int:
        total_records = 0
        async for batch in self._fetch_records_since(since):
            if batch:
                saved = await self._save_batch(batch)
                total_records += saved
                self.logger.info(
                    "Batch saved",
                    batch_size=len(batch),
                    saved=saved,
                    total=total_records
                )
        return total_records

    async def collect_last_hours(self, hours: int = 24) -> int:
        since = datetime.utcnow() - timedelta(hours=hours)
        return await self.collect_since(since)

    @abstractmethod
    async def _fetch_records_since(self, since: datetime) -> AsyncGenerator[List[Dict[str, Any]], None]:
        pass

    async def _save_batch(self, records: List[Dict[str, Any]]) -> int:
        saved_count = 0
        async with get_db_context() as db:
            for record in records:
                try:
                    result = await self._save_single_record(db, record)
                    if result:
                        saved_count += 1
                except Exception as e:
                    self.logger.error(
                        "Failed to save record",
                        error=str(e),
                        record_id=record.get("external_id")
                    )
                    continue
        return saved_count

    async def _save_single_record(self, db: AsyncSession, record: Dict[str, Any]) -> Optional[uuid.UUID]:
        external_id = record.get("external_id")
        if not external_id:
            return None

        existing = await db.execute(
            select(RawDataRecord).where(
                RawDataRecord.data_source == self.data_source_type.value,
                RawDataRecord.external_id == external_id
            )
        )
        if existing.scalar_one_or_none():
            return None

        raw_record = RawDataRecord(
            id=uuid.uuid4(),
            data_source=self.data_source_type.value,
            external_id=external_id,
            employee_identifier=record.get("employee_identifier"),
            recorded_at=record.get("recorded_at") or datetime.utcnow(),
            collected_at=datetime.utcnow(),
            raw_content=record.get("raw_content", {}),
            summary=record.get("summary"),
            tags=record.get("tags", []),
            is_processed=False,
            is_flagged=False,
        )
        db.add(raw_record)
        await db.flush()

        await self._save_specific_record(db, raw_record.id, record)
        return raw_record.id

    @abstractmethod
    async def _save_specific_record(
        self,
        db: AsyncSession,
        raw_data_id: uuid.UUID,
        record: Dict[str, Any]
    ) -> None:
        pass

    @staticmethod
    def generate_external_id(source_type: str, *parts: Any) -> str:
        raw = f"{source_type}:{':'.join(str(p) for p in parts)}"
        return hashlib.sha256(raw.encode()).hexdigest()
