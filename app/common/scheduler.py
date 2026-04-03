# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.domain.swing.trading.auto_swing_batch import (
    trade_job,
    us_trade_job,
    day_collect_job,
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
            hour='10-22',
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
    # 서머타임 KST 22:30-05:00 / 겨울 KST 23:30-06:00
    # 두 시간대를 모두 커버하는 범위: 23:00-05:30

    # 해외 지표 캐시 워밍업: 22:00 KST (미국 장 시작 전)
    scheduler.add_job(
        us_ema_cache_warmup_job,
        CronTrigger(minute='0', hour='22', day_of_week='mon-fri')
    )

    # 해외 매매 배치: 23:00-23:55 (월~금)
    scheduler.add_job(
        us_trade_job,
        CronTrigger(
            minute='*/5',
            hour='23',
            day_of_week='mon-fri'
        )
    )

    # 해외 매매 배치: 00:00-05:25 (화~토, 한국 기준 다음날)
    scheduler.add_job(
        us_trade_job,
        CronTrigger(
            minute='*/5',
            hour='0-5',
            day_of_week='tue-sat'
        )
    )

    scheduler.start()
