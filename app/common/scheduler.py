# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.domain.swing.trading.auto_swing_batch import (
    trade_job,
    day_collect_job,
    ema_cache_warmup_job,
    morning_sell_job
)

scheduler = AsyncIOScheduler()


async def schedule_start():
    # EMA 캐시 워밍업: 평일 08:30 (장 시작 전)
    scheduler.add_job(
        ema_cache_warmup_job,
        CronTrigger(minute='29', hour='8', day_of_week='mon-fri')
    )

    # 시초 매도 배치: 평일 09:00-09:55, 5분마다 실행
    # SIGNAL 4/5 상태의 스윙 시초 매도 실행
    scheduler.add_job(
        morning_sell_job,
        CronTrigger(
            minute='0,5,10,15,20,25,30,35,40,45,50,55',
            hour='9',
            day_of_week='mon-fri'
        )
    )

    # 스윙 트레이딩 배치 작업: 평일 10시-14시55분, 5분마다 실행
    # - 장중 매수 신호 확인 (SIGNAL 0 → 1, SIGNAL 1 → 2)
    # - 절대 손절(-3%) 체크 (SIGNAL 1/2 → 0)
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
    # - SIGNAL 1/2 → 종가 기준 매도 조건 판단 → SIGNAL 4/5로 전환
    scheduler.add_job(day_collect_job, CronTrigger(minute='35', hour='15', day_of_week='0-4'))

    scheduler.start()