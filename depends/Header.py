from fastapi import Header, HTTPException
from module.RedisConnection import redis_client
import json


async def session_token(Authorization: str = Header(...)):
    if not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    session_id = Authorization.split(" ")[1]  # Bearer 다음의 세션 ID 추출
    redis = await redis_client()
    session_data_str = await redis.get(session_id)

    if not session_data_str:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    try:
        session_data = json.loads(session_data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid session data format")

    return session_data
