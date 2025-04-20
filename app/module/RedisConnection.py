import redis.asyncio as aioredis
from typing import Optional
import os


class Redis:
    _instance: Optional[aioredis.Redis] = None

    @classmethod
    async def connect(cls) -> aioredis.Redis:
        """Redis 커넥션을 싱글톤으로 초기화하고 반환"""
        if cls._instance is None:
            redis_url = os.getenv("REDIS_URL")
            cls._instance = await aioredis.from_url(redis_url, decode_responses=True)
            await cls._instance.ping()  # 연결 테스트
        return cls._instance

    @classmethod
    async def disconnect(cls):
        """Redis 커넥션을 종료"""
        if cls._instance is not None:
            await cls._instance.close()
            cls._instance = None

    @classmethod
    async def get_connection(cls) -> aioredis.Redis:
        """Redis 커넥션을 반환 (필요 시 초기화)"""
        if cls._instance is None:
            await cls.connect()
        return cls._instance


# FastAPI 의존성 주입용 함수
async def get_redis():
    return await Redis.get_connection()


