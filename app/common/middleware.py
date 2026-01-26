"""
공통 미들웨어
"""
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.common.database import Database
from app.common.redis import Redis
from app.domain.device.repository import DeviceRepository
from app.exceptions import DeviceNotAllowedError

logger = logging.getLogger(__name__)


class DeviceAuthMiddleware(BaseHTTPMiddleware):
    """
    디바이스 ID 검증 미들웨어 (Redis 캐싱 포함)

    - X-Device-ID 헤더 필수
    - 활성화된 디바이스만 허용
    - Redis 캐시 활용 (TTL: 5분)
    - 캐시 미스 시 DB 조회
    """

    # 검증 제외 경로 (헬스체크, 문서, 루트)
    EXCLUDED_PATHS = ["/", "/health", "/docs", "/redoc", "/openapi.json"]

    async def dispatch(self, request: Request, call_next):
        # 제외 경로는 스킵
        if request.url.path in self.EXCLUDED_PATHS:
            return await call_next(request)

        # X-Device-ID 헤더 추출
        device_id = request.headers.get("X-Device-ID")
        if not device_id:
            logger.warning(f"X-Device-ID 헤더 누락: {request.url.path}")
            raise DeviceNotAllowedError(
                device_id=None,
                message="X-Device-ID 헤더가 필요합니다"
            )

        # Redis 캐시 조회
        redis_client = await Redis.get_connection()
        cache_key = f"device:allowed:{device_id}"
        is_allowed = await redis_client.get(cache_key)

        if is_allowed is None:  # 캐시 미스
            logger.debug(f"디바이스 캐시 미스: {device_id}")

            # DB 조회
            db = await Database.get_session()
            try:
                device_repo = DeviceRepository(db)
                device = await device_repo.find_active_device(device_id)

                # Redis에 캐싱
                if device and device.get("ACTIVE_YN") == "Y":
                    # 허용 (TTL 5분)
                    await redis_client.setex(cache_key, 300, "1")
                    is_allowed = "1"
                    logger.info(f"디바이스 허용 (캐시 저장): {device_id}")
                else:
                    # 거부 (TTL 1분 - 짧게 유지)
                    await redis_client.setex(cache_key, 60, "0")
                    is_allowed = "0"
                    logger.warning(f"디바이스 거부 (캐시 저장): {device_id}")

            finally:
                await db.close()
        else:
            logger.debug(f"디바이스 캐시 히트: {device_id} (allowed={is_allowed})")

        # 검증
        if is_allowed != "1":
            logger.warning(f"허용되지 않은 디바이스: {device_id}")
            raise DeviceNotAllowedError(device_id=device_id)

        # Request state에 디바이스 정보 저장 (선택적 활용)
        request.state.device_id = device_id

        return await call_next(request)