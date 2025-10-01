import pandas as pd
import asyncio
import os
import uuid
from datetime import datetime, UTC
from concurrent.futures import ThreadPoolExecutor
from dateutil.relativedelta import relativedelta
from app.stock.stock_service import get_day_stock_price
from app.swing.swing_model import SwingCreate
from app.swing.tech_analysis import ema_swing_signals
from sqlalchemy.ext.asyncio import AsyncSession
from app.module.redis_connection import get_redis
import json


# ===== 백테스트 잡 실행 환경 =====
_EXECUTOR = ThreadPoolExecutor(max_workers=min(4, (os.cpu_count() or 2)))
_BACKTEST_SEMAPHORE = asyncio.Semaphore(2)  # 동시에 실행될 백테스트 개수 제한
_JOBS: dict[str, dict] = {}  # 인메모리 잡 저장소 (운영에선 Redis/DB 권장)



def compute_backtest_sync(prices_df: pd.DataFrame, params: dict) -> dict:
    """
    CPU 바운드 백테스트 핵심 로직(동기). 이벤트 루프 밖에서 실행됨.
    """
    short_term = params["short_term"]
    medium_term = params["medium_term"]
    long_term = params["long_term"]
    initial_capital = params["swing_amount"]
    ris_period = params["rsi_period"]
    buy_ratio = params["buy_ratio"]
    sell_ratio = params["sell_ratio"]
    eval_start = params["eval_start"]

    df = prices_df.copy()
    eval_df = df[df["STCK_BSOP_DATE"] >= eval_start].copy()

    trades = []
    total_capital = initial_capital  # 총금액 기준 고정

    # 포지션 상태 계산(평단/수량)
    def get_position_state(exec_trades):
        position_qty = 0
        position_cost = 0.0
        for t in exec_trades:
            if t['action'] == 'BUY':
                position_cost += t['quantity'] * t['price']
                position_qty += t['quantity']
            elif t['action'] == 'SELL' and position_qty > 0:
                avg_cost = position_cost / position_qty
                sell_qty = t['quantity']
                position_cost -= avg_cost * sell_qty
                position_qty -= sell_qty
        avg_cost_now = (position_cost / position_qty) if position_qty > 0 else 0.0
        return position_qty, avg_cost_now

    for i in range(len(eval_df)):
        full_idx = df.index.get_loc(eval_df.index[i])
        current_data = df.iloc[: full_idx + 1]

        first_buy_signal, second_buy_signal, first_sell_signal, second_sell_signal = ema_swing_signals(
            current_data,
            short_term,
            medium_term,
            long_term
        )

        current_price = current_data['STCK_CLPR'].iloc[-1]
        current_date = current_data.index[-1] if hasattr(current_data.index, 'iloc') else i

        total_bought = sum(t['quantity'] for t in trades if t['action'] == 'BUY')
        total_sold = sum(t['quantity'] for t in trades if t['action'] == 'SELL')
        total_quantity = total_bought - total_sold

        # 1차 매수 (총금액 * BUY_RATIO)
        if first_buy_signal and initial_capital > 0 and total_quantity == 0:
            buy_amount = total_capital * buy_ratio
            buy_quantity = int(buy_amount / current_price)
            executed_amount = buy_quantity * current_price
            if buy_quantity > 0:
                initial_capital -= executed_amount
                trades.append({
                    'date': current_date,
                    'action': 'BUY',
                    'quantity': buy_quantity,
                    'price': current_price,
                    'amount': executed_amount,
                    'current_capital': initial_capital,
                    'reason': '1차 매수 신호',
                })

        # 2차 매수 (총금액 * BUY_RATIO, 남은 현금 한도 내)
        elif second_buy_signal and initial_capital > 0 and total_quantity > 0:
            buy_amount = min(total_capital * buy_ratio, initial_capital)
            buy_quantity = int(buy_amount / current_price)
            executed_amount = buy_quantity * current_price
            if buy_quantity > 0 and executed_amount > 0:
                initial_capital -= executed_amount
                trades.append({
                    'date': current_date,
                    'action': 'BUY',
                    'quantity': buy_quantity,
                    'price': current_price,
                    'amount': executed_amount,
                    'current_capital': initial_capital,
                    'reason': '2차 매수 신호'
                })

        # 매도 신호 처리
        elif first_sell_signal or second_sell_signal:
            total_bought = sum(t['quantity'] for t in trades if t['action'] == 'BUY')
            total_sold = sum(t['quantity'] for t in trades if t['action'] == 'SELL')
            total_quantity = total_bought - total_sold

            if total_quantity > 0:
                curr_qty, curr_avg_cost = get_position_state(trades)

                if first_sell_signal:
                    sell_quantity = int(total_quantity * sell_ratio)
                    if sell_quantity > 0:
                        sell_amount = sell_quantity * current_price
                        realized_pnl = (current_price - curr_avg_cost) * sell_quantity
                        realized_pnl_pct = ((current_price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0
                        initial_capital += sell_amount
                        trades.append({
                            'date': current_date,
                            'action': 'SELL',
                            'quantity': sell_quantity,
                            'price': current_price,
                            'amount': sell_amount,
                            'current_capital': initial_capital,
                            'realized_pnl': realized_pnl,
                            'realized_pnl_pct': realized_pnl_pct,
                            'reason': '1차 매도 신호'
                        })
                elif second_sell_signal:
                    sell_quantity = curr_qty
                    sell_amount = sell_quantity * current_price
                    realized_pnl = (current_price - curr_avg_cost) * sell_quantity
                    realized_pnl_pct = ((current_price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0
                    initial_capital += sell_amount
                    trades.append({
                        'date': current_date,
                        'action': 'SELL',
                        'quantity': sell_quantity,
                        'price': current_price,
                        'amount': sell_amount,
                        'current_capital': initial_capital,
                        'realized_pnl': realized_pnl,
                        'realized_pnl_pct': realized_pnl_pct,
                        'reason': '2차 매도 신호 - 전량 매도'
                    })

    final_capital = initial_capital
    total_return = 0.0  # 상세 수익률 집계는 trades 기반으로 별도 계산 가능

    return {
        "strategy_name": "스윙 전략 (오프로딩 백테스트)",
        "start_date": str(prices_df["STCK_BSOP_DATE"].min()),
        "end_date": str(prices_df["STCK_BSOP_DATE"].max()),
        "initial_capital": params["swing_amount"],
        "final_capital": final_capital,
        "total_return": total_return,
        "total_trades": len(trades),
        "parameters": {
            "ST_CODE": params["st_code"],
            "SHORT_TERM": short_term,
            "MEDIUM_TERM": medium_term,
            "LONG_TERM": long_term
        },
        "trades": trades,
    }


async def compute_backtest_offloaded(prices_df: pd.DataFrame, params: dict) -> dict:
    loop = asyncio.get_running_loop()
    async with _BACKTEST_SEMAPHORE:
        return await loop.run_in_executor(_EXECUTOR, compute_backtest_sync, prices_df, params)


async def start_backtest_job(db: AsyncSession, swing_data: SwingCreate) -> str:
    """
    비동기 백테스트 잡 시작:
    - 필요한 데이터만 로드(1년 + 지표 룩백)
    - CPU 연산은 쓰레드풀로 오프로딩
    - job_id 반환
    """
    if not swing_data.ST_CODE:
        raise ValueError("주식 코드(ST_CODE)는 필수입니다.")

    short_term = swing_data.SHORT_TERM
    medium_term = swing_data.MEDIUM_TERM
    long_term = swing_data.LONG_TERM
    swing_amount = swing_data.SWING_AMOUNT
    rsi_period = swing_data.RSI_PERIOD
    buy_ratio = swing_data.BUY_RATIO / 100
    sell_ratio = swing_data.SELL_RATIO / 100

    end_date = datetime.now(UTC)
    start_date = end_date - relativedelta(years=3)

    # 실제 백테스팅은 1년치만 실행
    eval_start = end_date - relativedelta(years=1)

    # 3년치 주가 데이터 조회
    price_days = await get_day_stock_price(db, swing_data.ST_CODE, start_date)
    if not price_days:
        raise ValueError("주가 데이터가 없습니다.")

    prices_df = pd.DataFrame(price_days)

    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {
        "status": "queued",
        "result": None,
        "error": None,
        "created_at": datetime.now(UTC).isoformat()
    }

    params = {
        "st_code": swing_data.ST_CODE,
        "short_term": short_term,
        "medium_term": medium_term,
        "long_term": long_term,
        "swing_amount": swing_amount,
        "rsi_period": rsi_period,
        "buy_ratio": buy_ratio,
        "sell_ratio": sell_ratio,
        "eval_start": eval_start,
    }

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


# Redis 키 유틸
def job_key(job_id: str) -> str:
    return f"backtest:{job_id}"

async def job_create(job_id: str) -> None:
    redis = await get_redis()
    now = datetime.now(UTC).isoformat()
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
    # 필요 시 TTL(초) 적용: 24시간
    await redis.expire(job_key(job_id), 60 * 60 * 24)

async def job_set_status(job_id: str, status: str) -> None:
    redis = await get_redis()
    await redis.hset(
        job_key(job_id),
        mapping={
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )

async def job_set_result(job_id: str, result: dict | None = None, error: str | None = None) -> None:
    redis = await get_redis()
    mapping = {"updated_at": datetime.now(UTC).isoformat()}
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
