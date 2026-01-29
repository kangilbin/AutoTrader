import pandas as pd
import asyncio
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.stock.service import StockService
from .strategy_factory import StrategyFactory
from app.domain.swing.schemas import SwingCreateRequest

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
    swing_type = params.get("swing_type", "S")

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


async def run_backtest(db: AsyncSession, swing_data: SwingCreateRequest) -> dict:
    """백테스트 실행 및 결과 반환"""
    if not swing_data.MRKT_CODE:
        raise ValueError("시장 코드는 필수입니다.")

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
    stock_service = StockService(db)
    price_days = await stock_service.get_stock_history(swing_data.MRKT_CODE, swing_data.ST_CODE, start_date)
    if not price_days:
        raise ValueError("주가 데이터가 없습니다.")

    prices_df = pd.DataFrame(price_days)

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

    return await compute_backtest_offloaded(prices_df, params)
