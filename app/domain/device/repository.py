"""
Device Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List

from app.domain.device.entity import Device
from app.domain.device.schemas import DeviceResponse
import logging

logger = logging.getLogger(__name__)


class DeviceRepository:
    """디바이스 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, device_id: str) -> Optional[dict]:
        """디바이스 조회"""
        query = select(Device).filter(Device.DEVICE_ID == device_id)
        result = await self.db.execute(query)
        db_device = result.scalars().first()
        if not db_device:
            return None
        return DeviceResponse.model_validate(db_device).model_dump()

    async def find_active_device(self, device_id: str) -> Optional[dict]:
        """활성화된 디바이스 조회 (미들웨어용)"""
        query = select(Device).filter(
            Device.DEVICE_ID == device_id,
            Device.ACTIVE_YN == 'Y'
        )
        result = await self.db.execute(query)
        db_device = result.scalars().first()
        if not db_device:
            return None
        return DeviceResponse.model_validate(db_device).model_dump()

    async def save(self, device_id: str, device_name: str, user_id: Optional[str] = None) -> Device:
        """디바이스 저장 (flush만 수행)"""
        db_device = Device(
            DEVICE_ID=device_id,
            DEVICE_NAME=device_name,
            USER_ID=user_id,
            ACTIVE_YN='Y'
        )
        self.db.add(db_device)
        await self.db.flush()
        await self.db.refresh(db_device)
        return db_device

    async def save_inactive(self, device_id: str, device_name: str, user_id: str) -> Device:
        """디바이스 비활성 상태로 저장 - 승인 대기 (flush만 수행)"""
        db_device = Device(
            DEVICE_ID=device_id,
            DEVICE_NAME=device_name,
            USER_ID=user_id,
            ACTIVE_YN='N'
        )
        self.db.add(db_device)
        await self.db.flush()
        await self.db.refresh(db_device)
        return db_device
