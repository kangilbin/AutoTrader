"""
Device Repository - 데이터 접근 계층
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from typing import Optional, List

from app.common.database import DeviceModel
from app.domain.device.schemas import DeviceResponse
import logging

logger = logging.getLogger(__name__)


class DeviceRepository:
    """디바이스 Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, device_id: str) -> Optional[dict]:
        """디바이스 조회"""
        query = select(DeviceModel).filter(DeviceModel.DEVICE_ID == device_id)
        result = await self.db.execute(query)
        db_device = result.scalars().first()
        if not db_device:
            return None
        return DeviceResponse.model_validate(db_device).model_dump()

    async def find_active_device(self, device_id: str) -> Optional[dict]:
        """활성화된 디바이스 조회 (미들웨어용)"""
        query = select(DeviceModel).filter(
            DeviceModel.DEVICE_ID == device_id,
            DeviceModel.ACTIVE_YN == 'Y'
        )
        result = await self.db.execute(query)
        db_device = result.scalars().first()
        if not db_device:
            return None
        return DeviceResponse.model_validate(db_device).model_dump()

    async def find_all(self) -> List[dict]:
        """디바이스 목록 조회"""
        query = select(DeviceModel).order_by(DeviceModel.REG_DT.desc())
        result = await self.db.execute(query)
        db_devices = result.scalars().all()
        return [DeviceResponse.model_validate(device).model_dump() for device in db_devices]

    async def find_by_user(self, user_id: str) -> List[dict]:
        """사용자별 디바이스 목록 조회"""
        query = select(DeviceModel).filter(
            DeviceModel.USER_ID == user_id
        ).order_by(DeviceModel.REG_DT.desc())
        result = await self.db.execute(query)
        db_devices = result.scalars().all()
        return [DeviceResponse.model_validate(device).model_dump() for device in db_devices]

    async def save(self, device_id: str, device_name: str, user_id: Optional[str] = None) -> DeviceModel:
        """디바이스 저장 (flush만 수행)"""
        db_device = DeviceModel(
            DEVICE_ID=device_id,
            DEVICE_NAME=device_name,
            USER_ID=user_id,
            ACTIVE_YN='Y'
        )
        self.db.add(db_device)
        await self.db.flush()
        await self.db.refresh(db_device)
        return db_device

    async def update(self, device_id: str, data: dict) -> Optional[DeviceModel]:
        """디바이스 수정 (flush만 수행)"""
        query = (
            update(DeviceModel)
            .filter(DeviceModel.DEVICE_ID == device_id)
            .values(**data)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(query)
        await self.db.flush()
        return await self.db.get(DeviceModel, device_id)

    async def delete(self, device_id: str) -> bool:
        """디바이스 삭제 (flush만 수행)"""
        query = delete(DeviceModel).filter(DeviceModel.DEVICE_ID == device_id)
        result = await self.db.execute(query)
        await self.db.flush()
        return result.rowcount > 0

    async def exists(self, device_id: str) -> bool:
        """디바이스 존재 여부 확인"""
        query = select(DeviceModel.DEVICE_ID).filter(DeviceModel.DEVICE_ID == device_id)
        result = await self.db.execute(query)
        return result.scalar() is not None