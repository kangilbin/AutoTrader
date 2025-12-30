# app/core/health.py
import asyncio
from typing import Any, Dict, Optional

from sqlalchemy import text

from app.common.database import Database
from app.module.redis_connection import Redis


async def _check_redis(timeout_sec: float) -> Dict[str, Any]:
    try:
        ok = await asyncio.wait_for(Redis.health_check(), timeout=timeout_sec)
        return {"ok": bool(ok)}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


async def _check_db(timeout_sec: float) -> Dict[str, Any]:
    db = None
    try:
        db = await Database.get_session()
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=timeout_sec)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}
    finally:
        if db is not None:
            await db.close()


async def readiness_status(
    *,
    timeout_sec: float = 1.0,
    require_redis: bool = True,
    require_db: bool = True,
) -> Dict[str, Any]:
    """
    readiness 결과를 표준 dict로 반환.
    - require_redis/require_db로 "필수 의존성" 정책을 바꿀 수 있음
    """
    redis_task = _check_redis(timeout_sec)
    db_task = _check_db(timeout_sec)

    redis_result, db_result = await asyncio.gather(redis_task, db_task)

    checks = {
        "redis": redis_result,
        "db": db_result,
    }

    required_ok = True
    if require_redis:
        required_ok = required_ok and bool(checks["redis"]["ok"])
    if require_db:
        required_ok = required_ok and bool(checks["db"]["ok"])

    return {
        "status": "ready" if required_ok else "not_ready",
        "checks": checks,
        "required": {
            "redis": require_redis,
            "db": require_db,
        },
    }