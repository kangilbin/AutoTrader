import aioredis
from main import app


async def create_redis():
    # Redis 클라이언트 인스턴스 생성
    redis = await aioredis.from_url("redis://localhost:6379", decode_responses=True)

    return redis


def get_redis():
    return app.state.redis

