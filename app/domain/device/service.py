"""
Device Service - 비즈니스 로직 + 트랜잭션 관리
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from typing import List
import logging

from app.domain.device.repository import DeviceRepository
from app.domain.device.schemas import DeviceCreateRequest, DeviceUpdateRequest, DeviceResponse
from app.domain.device.entity import Device
from app.exceptions import NotFoundError, DuplicateError
from app.common.redis import Redis

logger = logging.getLogger(__name__)


class DeviceService:
    """디바이스 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = DeviceRepository(db)

    async def create_device(self, request: DeviceCreateRequest) -> dict:
        """디바이스 생성"""
        try:
            # 도메인 엔티티 생성 (비즈니스 검증)
            device = Device.create(
                device_id=request.DEVICE_ID,
                device_name=request.DEVICE_NAME,
                user_id=request.USER_ID
            )

            # 저장
            db_device = await self.repo.save(
                device_id=device.device_id,
                device_name=device.device_name,
                user_id=device.user_id
            )
            await self.db.commit()

            return DeviceResponse.model_validate(db_device).model_dump()

        except IntegrityError:
            await self.db.rollback()
            raise DuplicateError("디바이스", request.DEVICE_ID)
        except Exception as e:
            await self.db.rollback()
            logger.error(f"디바이스 생성 실패: {e}")
            raise

    async def get_device(self, device_id: str) -> dict:
        """디바이스 조회"""
        device = await self.repo.find_by_id(device_id)
        if not device:
            raise NotFoundError("디바이스", device_id)
        return device

    async def get_all_devices(self) -> List[dict]:
        """디바이스 목록 조회"""
        return await self.repo.find_all()

    async def get_user_devices(self, user_id: str) -> List[dict]:
        """사용자별 디바이스 목록 조회"""
        return await self.repo.find_by_user(user_id)

    async def update_device(self, device_id: str, request: DeviceUpdateRequest) -> dict:
        """디바이스 수정 + Redis 캐시 무효화"""
        try:
            # 존재 여부 확인
            existing = await self.repo.find_by_id(device_id)
            if not existing:
                raise NotFoundError("디바이스", device_id)

            # 수정 데이터 준비
            update_data = {}
            if request.DEVICE_NAME:
                update_data["DEVICE_NAME"] = request.DEVICE_NAME
            if request.USER_ID is not None:
                update_data["USER_ID"] = request.USER_ID
            if request.ACTIVE_YN:
                update_data["ACTIVE_YN"] = request.ACTIVE_YN

            # 수정
            db_device = await self.repo.update(device_id, update_data)
            await self.db.commit()

            # Redis 캐시 무효화 (즉시 반영)
            await self._invalidate_cache(device_id)

            return DeviceResponse.model_validate(db_device).model_dump()

        except NotFoundError:
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"디바이스 수정 실패: {e}")
            raise

    async def delete_device(self, device_id: str) -> bool:
        """디바이스 삭제 + Redis 캐시 무효화"""
        try:
            # 존재 여부 확인
            existing = await self.repo.find_by_id(device_id)
            if not existing:
                raise NotFoundError("디바이스", device_id)

            # 삭제
            result = await self.repo.delete(device_id)
            await self.db.commit()

            # Redis 캐시 무효화
            await self._invalidate_cache(device_id)

            return result

        except NotFoundError:
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"디바이스 삭제 실패: {e}")
            raise

    async def _invalidate_cache(self, device_id: str):
        """Redis 캐시 무효화"""
        try:
            redis_client = await Redis.get_connection()
            cache_key = f"device:allowed:{device_id}"
            await redis_client.delete(cache_key)
            logger.info(f"디바이스 캐시 무효화: {device_id}")
        except Exception as e:
            logger.warning(f"캐시 무효화 실패 (계속 진행): {e}")