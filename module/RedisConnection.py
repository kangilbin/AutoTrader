import aioredis
from main import app


async def redis_pool(max_size: int = 10):
    return await aioredis.create_redis_pool(
        "redis://localhost:6379", minsize=5, maxsize=max_size, decode_responses=True
    )


def redis():
    return app.state.redis_pool

