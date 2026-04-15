# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.domain.swing.trading.auto_swing_batch import (
    trade_job,
    us_trade_job,
    day_collect_job,
    us_day_collect_job,
    ema_cache_warmup_job,
    us_ema_cache_warmup_job,
)

scheduler = AsyncIOScheduler()


async def schedule_start():
    # === 국내 장 스케줄 ===

    # 지표 캐시 워밍업 (EMA20, ADX, DI): 평일 08:29 (장 시작 전)
    scheduler.add_job(
        ema_cache_warmup_job,
        CronTrigger(minute='29', hour='8', day_of_week='mon-fri')
    )

    # 스윙 트레이딩 배치 작업: 평일 10시-14시59분, 5분마다 실행
    scheduler.add_job(
        trade_job,
        CronTrigger(
            minute='*/5',
            hour='8-14',
            day_of_week='mon-fri'
        )
    )

    # 장 마감 전 추가 실행: 평일 15시00분-15시20분, 5분마다
    scheduler.add_job(
        trade_job,
        CronTrigger(
            minute='0,5,10,15,20',
            hour='15',
            day_of_week='mon-fri'
        )
    )

    # 일일 데이터 수집 + 종가 매도 신호 확정 (장 마감 후)
    # - 당일 OHLCV 데이터 저장
    # - SIGNAL 1/2 → 종가 기준 EOD 매도 조건 신호 저장
    scheduler.add_job(day_collect_job, CronTrigger(minute='35', hour='15', day_of_week='0-4'))

    # === 미국 장 스케줄 ===
    # 미국 동부시간(ET) 기준 설정 → 서머타임/겨울시간 자동 반영
    # 정규장: 09:30-16:00 ET
    # 개장 후 1.5시간 버퍼 적용 → 11:00 ET부터 매매 시작
    us_tz = 'America/New_York'

    # 해외 지표 캐시 워밍업: 09:00 ET (미국 장 시작 30분 전)
    scheduler.add_job(
        us_ema_cache_warmup_job,
        CronTrigger(minute='0', hour='9', day_of_week='mon-fri', timezone=us_tz)
    )

    # 해외 스윙 매매 배치: 11:00-15:55 ET (월~금), 5분마다
    scheduler.add_job(
        us_trade_job,
        CronTrigger(
            minute='*/5',
            hour='11-15',
            day_of_week='mon-fri',
            timezone=us_tz
        )
    )

    # 미국 일일 데이터 수집 (미국장 마감 후, 16:35 ET)
    scheduler.add_job(
        us_day_collect_job,
        CronTrigger(minute='35', hour='16', day_of_week='mon-fri', timezone=us_tz)
    )

    scheduler.start()
