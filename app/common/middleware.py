"""
공통 미들웨어
"""
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.common.database import Database
from app.common.redis import Redis
from app.domain.device.repository import DeviceRepository
from app.exceptions import AppError, DeviceNotAllowedError
from app.exceptions.handlers import app_error_response

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
    # /favicon.ico: 브라우저가 페이지 열 때 자동 요청하는 경로. 앱 라우트가 아니라
    #               제외하지 않으면 브라우저로 /health 등을 열 때마다 에러 로그가 남는다.
    # frozenset: 매 요청마다 조회하므로 리스트 선형탐색 대신 해시 조회를 쓴다
    EXCLUDED_PATHS = frozenset({
        "/", "/health", "/ready",
        "/docs", "/redoc", "/openapi.json",
        "/oauth/google/login",
        "/favicon.ico",
    })

    async def dispatch(self, request: Request, call_next):
        # 제외 경로는 스킵
        if request.url.path in self.EXCLUDED_PATHS:
            return await call_next(request)

        try:
            await self._verify_device(request)
        except AppError as e:
            # BaseHTTPMiddleware 는 FastAPI 의 ExceptionMiddleware 바깥에서 실행되므로
            # @app.exception_handler(AppError) 가 닿지 않는다. 여기서 던지면 미처리 예외로
            # 취급되어 403 이어야 할 접근 거부가 500 + 트레이스백 + Sentry 이벤트가 된다.
            # (공개 포트로 들어오는 스캐너 요청마다 발생) → 경계에서 직접 응답으로 변환한다.
            return app_error_response(request, e)

        return await call_next(request)

    async def _verify_device(self, request: Request) -> None:
        """디바이스 검증 — 실패 시 AppError 를 던진다 (변환은 dispatch 가 담당)"""
        # X-Device-ID 헤더 추출
        device_id = request.headers.get("X-Device-ID")
        if not device_id:
            # UA 는 클라이언트가 임의로 넣는 값 → 로그 부풀림 방지로 길이 제한
            ua = request.headers.get("user-agent", "-")[:120]
            logger.warning(f"X-Device-ID 헤더 누락: {request.url.path} UA={ua}")
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

        # 검증
        if is_allowed != "1":
            logger.warning(f"허용되지 않은 디바이스: {device_id}")
            raise DeviceNotAllowedError(device_id=device_id)

        # Request state에 디바이스 정보 저장 (선택적 활용)
        request.state.device_id = device_id