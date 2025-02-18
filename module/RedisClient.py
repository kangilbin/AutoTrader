import aioredis
import asyncio

# Redis 클라이언트 연결 풀
redis = None


# 비동기 Redis 클라이언트 반환 함수
async def redis_client():
    global redis
    if redis is None:
        # aioredis 2.x 이상에서는 from_url을 사용하여 클라이언트 생성
        redis = await aioredis.from_url("redis://localhost:6379", decode_responses=True)
    return redis


# 세션 정보를 가져오고 만료 시간 갱신하는 함수
async def session_info(session_id: str, ttl_seconds: int = 3600):
    """
    세션 ID로 세션 정보를 가져오고 만료 시간을 갱신
    :param session_id: 세션 ID
    :param ttl_seconds: 세션 만료 시간 (초 단위, 기본 1시간)
    :return: 세션 정보 (없으면 None)
    """
    session_redis = await redis_client()
    session = await session_redis.get(session_id)
    if session:
        await session_redis.expire(session_id, ttl_seconds)
    return session
