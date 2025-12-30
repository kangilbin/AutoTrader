"""
Redis 연결 관리 - 연결 풀 설정
"""
import redis.asyncio as aioredis
from typing import Optional
from app.core.config import get_settings
import logging

logger = logging.getLogger(__name__)


class Redis:
    _instance: Optional[aioredis.Redis] = None

    @classmethod
    async def connect(cls) -> aioredis.Redis:
        """Redis 커넥션을 싱글톤으로 초기화하고 반환 (연결 풀 사용)"""
        if cls._instance is None:
            settings = get_settings()
            cls._instance = await aioredis.from_url(
                settings.REDIS_URL,
                password=settings.REDIS_PASSWORD,
                decode_responses=True,
                # 연결 풀 설정
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            await cls._instance.ping()  # 연결 테스트
            logger.info(f"Redis 연결 성공 (max_connections: {settings.REDIS_MAX_CONNECTIONS})")
        return cls._instance

    @classmethod
    async def disconnect(cls):
        """Redis 커넥션을 종료"""
        if cls._instance is not None:
            await cls._instance.close()
            cls._instance = None
            logger.info("Redis 연결 종료")

    @classmethod
    async def get_connection(cls) -> aioredis.Redis:
        """Redis 커넥션을 반환 (필요 시 초기화)"""
        if cls._instance is None:
            await cls.connect()
        return cls._instance

    @classmethod
    async def health_check(cls) -> bool:
        """Redis 연결 상태 확인"""
        try:
            if cls._instance is None:
                return False
            await cls._instance.ping()
            return True
        except Exception as e:
            logger.error(f"Redis 헬스체크 실패: {e}")
            return False


# FastAPI 의존성 주입용 함수
async def get_redis() -> aioredis.Redis:
    """Redis 커넥션 반환"""
    return await Redis.get_connection()
