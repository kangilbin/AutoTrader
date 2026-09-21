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
from app.exceptions.domain import ValidationError

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


# 평가구간에 최소한 이만큼은 있어야 "검증했다"고 말할 수 있다. 워밍업만 채우고
# 평가할 봉이 몇 개뿐이면 거래가 0~1건이라 결과가 통계적으로 무의미하다.
MIN_EVAL_BARS = 60


def _describe_range(price_days: list, eval_start: datetime) -> dict:
    """조회된 봉의 구간·개수 요약 (거래일 기준 — 달력 일수로 환산하지 않는다)"""
    dates = sorted(str(row.get("STCK_BSOP_DATE") or "") for row in price_days)
    eval_key = eval_start.strftime("%Y%m%d")
    return {
        "start": dates[0] if dates else None,
        "end": dates[-1] if dates else None,
        "total": len(price_days),
        "eval": sum(1 for d in dates if d >= eval_key),
    }


def _validate_sufficiency(swing_type: str, required: int, data_range: dict) -> None:
    """데이터 충분성 검증

    부족한 데이터로 계산하면 지표가 NaN 이거나(EMA 계열), 신호가 전부 False 로
    나온다(일목균형표는 길이 가드에서 조용히 그렇게 한다). 후자는 '거래 0건'이라는
    정상 결과처럼 보이므로, 계산을 시작하기 전에 막는다.
    """
    total, evaluated = data_range["total"], data_range["eval"]

    if total < required:
        raise ValidationError(
            f"전략 '{swing_type}' 백테스트에 최소 {required}봉이 필요하지만 "
            f"{total}봉만 있습니다 "
            f"(보유 구간 {data_range['start']}~{data_range['end']}). "
            f"스윙을 활성화하면 3년치 데이터를 적재합니다.",
            field="ST_CODE",
        )

    if evaluated < MIN_EVAL_BARS:
        raise ValidationError(
            f"평가 구간 데이터가 부족합니다. 최소 {MIN_EVAL_BARS}봉이 필요하지만 "
            f"{evaluated}봉만 있습니다 "
            f"(전체 {total}봉 중 워밍업을 제외한 구간). "
            f"데이터 적재 후 다시 시도해주세요.",
            field="ST_CODE",
        )


async def run_backtest(db: AsyncSession, swing_data: SwingCreateRequest) -> dict:
    """백테스트 실행 및 결과 반환"""
    if not swing_data.MRKT_CODE:
        raise ValidationError("시장 코드는 필수입니다.", field="MRKT_CODE")

    if not swing_data.ST_CODE:
        raise ValidationError("주식 코드는 필수입니다.", field="ST_CODE")

    if not swing_data.SWING_TYPE:
        raise ValidationError("전략 타입은 필수입니다.", field="SWING_TYPE")

    # 전략 타입 검증
    available_strategies = StrategyFactory.get_available_strategies()
    if swing_data.SWING_TYPE not in available_strategies:
        raise ValidationError(
            f"지원하지 않는 전략 타입: {swing_data.SWING_TYPE}. "
            f"사용 가능한 타입: {available_strategies}",
            field="SWING_TYPE"
        )

    short_term = swing_data.SHORT_TERM or 5
    medium_term = swing_data.MEDIUM_TERM or 20
    long_term = swing_data.LONG_TERM or 60
    init_amount = swing_data.INIT_AMOUNT

    end_date = datetime.now()
    start_date = end_date - relativedelta(years=3)
    eval_start = end_date - relativedelta(years=2)

    # 주가 데이터 조회
    stock_service = StockService(db)
    price_days = await stock_service.get_stock_history(swing_data.MRKT_CODE, swing_data.ST_CODE, start_date)
    if not price_days:
        raise ValidationError(
            "주가 데이터가 없습니다. 스윙을 활성화하면 3년치 데이터를 적재합니다.",
            field="ST_CODE",
        )

    params_for_min = {"long_term": long_term}
    required = StrategyFactory.get_strategy(swing_data.SWING_TYPE).min_bars(params_for_min)
    data_range = _describe_range(price_days, eval_start)
    _validate_sufficiency(swing_data.SWING_TYPE, required, data_range)

    prices_df = pd.DataFrame(price_days)

    params = {
        "st_code": swing_data.ST_CODE,
        "swing_type": swing_data.SWING_TYPE,
        "short_term": short_term,
        "medium_term": medium_term,
        "long_term": long_term,
        "init_amount": init_amount,
        "eval_start": eval_start,
    }

    backtest_result = await compute_backtest_offloaded(prices_df, params)

    # 몇 봉으로 계산했는지 응답만 보고 알 수 있어야 한다. 3년을 요청해도 실제
    # 보유 구간이 짧으면 결과의 의미가 달라지는데, 지금까지는 드러나지 않았다.
    backtest_result["DATA_RANGE"] = {
        "START_DATE": data_range["start"],
        "END_DATE": data_range["end"],
        "TOTAL_BARS": data_range["total"],
        "EVAL_BARS": data_range["eval"],
        "REQUIRED_BARS": required,
    }
    return backtest_result
