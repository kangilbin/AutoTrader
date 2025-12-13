import pandas as pd
import asyncio
import os
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dateutil.relativedelta import relativedelta
from app.stock.stock_service import get_day_stock_price
from app.swing.strategy_factory import StrategyFactory
from app.swing.swing_model import SwingCreate
from sqlalchemy.ext.asyncio import AsyncSession
from app.module.redis_connection import get_redis
import json

# ===== 백테스트 잡 실행 환경 =====
_EXECUTOR = ThreadPoolExecutor(max_workers=min(4, (os.cpu_count() or 2)))
_BACKTEST_SEMAPHORE = asyncio.Semaphore(2)


async def compute_backtest_offloaded(prices_df: pd.DataFrame, params: dict) -> dict:
    """
    전략 타입에 따라 적절한 백테스트를 스레드에서 실행

    Args:
        prices_df: 주가 데이터
        params: 전략 파라미터 (swing_type 포함)

    Returns:
        백테스트 결과
    """
    swing_type = params.get("swing_type", "A")

    # 전략 팩토리에서 전략 객체 가져오기
    strategy = StrategyFactory.get_strategy(swing_type)

    loop = asyncio.get_running_loop()
    async with _BACKTEST_SEMAPHORE:
        # 전략의 compute 메서드를 스레드에서 실행
        return await loop.run_in_executor(
            _EXECUTOR,
            strategy.compute,
            prices_df,
            params
        )


async def start_backtest_job(db: AsyncSession, swing_data: SwingCreate) -> str:
    """
    비동기 백테스트 잡 시작
    """
    if not swing_data.ST_CODE:
        raise ValueError("주식 코드는 필수입니다.")

    if not swing_data.SWING_TYPE:
        raise ValueError("전략 타입은 필수입니다.")

    # 전략 타입 검증
    available_strategies = StrategyFactory.get_available_strategies()
    if swing_data.SWING_TYPE not in available_strategies:
        raise ValueError(
            f"지원하지 않는 전략 타입: {swing_data.SWING_TYPE}. "
            f"사용 가능한 타입: {available_strategies}"
        )

    short_term = swing_data.SHORT_TERM or 5
    medium_term = swing_data.MEDIUM_TERM or 20
    long_term = swing_data.LONG_TERM or 60
    init_amount = swing_data.INIT_AMOUNT
    buy_ratio = (swing_data.BUY_RATIO or 50) / 100
    sell_ratio = (swing_data.SELL_RATIO or 50) / 100

    end_date = datetime.now()
    start_date = end_date - relativedelta(years=3)
    eval_start = end_date - relativedelta(years=1)

    # 주가 데이터 조회
    price_days = await get_day_stock_price(db, swing_data.ST_CODE, start_date)
    if not price_days:
        raise ValueError("주가 데이터가 없습니다.")

    prices_df = pd.DataFrame(price_days)

    job_id = uuid.uuid4().hex

    params = {
        "st_code": swing_data.ST_CODE,
        "swing_type": swing_data.SWING_TYPE,
        "short_term": short_term,
        "medium_term": medium_term,
        "long_term": long_term,
        "init_amount": init_amount,
        "buy_ratio": buy_ratio,
        "sell_ratio": sell_ratio,
        "eval_start": eval_start,
    }

    # 잡 생성
    await job_create(job_id)

    async def runner():
        await job_set_status(job_id, "running")
        try:
            result = await compute_backtest_offloaded(prices_df, params)
            await job_set_result(job_id, result={"success": True, "message": "백테스팅 완료", "data": result})
            await job_set_status(job_id, "done")
        except Exception as e:
            await job_set_result(job_id, result=None, error=str(e))
            await job_set_status(job_id, "error")

    asyncio.create_task(runner())
    return job_id


# ===== Redis 잡 관리 함수 =====

def job_key(job_id: str) -> str:
    return f"backtest:{job_id}"


async def job_create(job_id: str) -> None:
    redis = await get_redis()
    now = datetime.now().isoformat()
    await redis.hset(
        job_key(job_id),
        mapping={
            "status": "queued",
            "result": "",
            "error": "",
            "created_at": now,
            "updated_at": now,
        },
    )
    await redis.expire(job_key(job_id), 60 * 60 * 24)


async def job_set_status(job_id: str, status: str) -> None:
    redis = await get_redis()
    await redis.hset(
        job_key(job_id),
        mapping={
            "status": status,
            "updated_at": datetime.now().isoformat(),
        },
    )


async def job_set_result(job_id: str, result: dict | None = None, error: str | None = None) -> None:
    redis = await get_redis()
    mapping = {"updated_at": datetime.now().isoformat()}
    if result is not None:
        mapping["result"] = json.dumps(result, ensure_ascii=False)
    if error is not None:
        mapping["error"] = error
    await redis.hset(job_key(job_id), mapping=mapping)


async def get_backtest_job(job_id: str) -> dict:
    redis = await get_redis()
    data = await redis.hgetall(job_key(job_id))
    if not data:
        return {"status": "not_found", "result": None, "error": "job_id 없음"}
    result = data.get("result")
    return {
        "status": data.get("status"),
        "result": json.loads(result) if result else None,
        "error": data.get("error") or None,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }
