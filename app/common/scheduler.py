# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.domain.swing.trading.auto_swing_batch import (
    trade_job,
    day_collect_job,
    ema_cache_warmup_job,
)

scheduler = AsyncIOScheduler()


async def schedule_start():
    # 지표 캐시 워밍업 (EMA20, ADX, DI): 평일 08:29 (장 시작 전)
    scheduler.add_job(
        ema_cache_warmup_job,
        CronTrigger(minute='29', hour='8', day_of_week='mon-fri')
    )

    # 스윙 트레이딩 배치 작업: 평일 10시-14시55분, 5분마다 실행
    scheduler.add_job(
        trade_job,
        CronTrigger(
            minute='*/5',      # 5분마다
            hour='10-14',      # 10시-14시 59분
            day_of_week='mon-fri'  # 월-금
        )
    )

    # 일일 데이터 수집 + 종가 매도 신호 확정 (장 마감 후)
    # - 당일 OHLCV 데이터 저장
    # - SIGNAL 1/2 → 종가 기준 EOD 매도 조건 신호 저장
    scheduler.add_job(day_collect_job, CronTrigger(minute='35', hour='15', day_of_week='0-4'))

    scheduler.start()