# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.domain.swing.auto_swing_batch import trade_job, day_collect_job, ema_cache_warmup_job

scheduler = AsyncIOScheduler()


async def schedule_start():
    # EMA 캐시 워밍업: 평일 08:30 (장 시작 전)
    scheduler.add_job(
        ema_cache_warmup_job,
        CronTrigger(minute='30', hour='8', day_of_week='mon-fri')
    )

    # 스윙 트레이딩 배치 작업: 평일 10시-15시, 5분마다 실행
    # 10시 시작: 시초가 수급 왜곡 구간(09:00-09:30) 회피
    scheduler.add_job(
        trade_job,
        CronTrigger(
            minute='*/5',      # 5분마다 (0,5,10,15,20,25,30,35,40,45,50,55)
            hour='10-14',      # 10시-14시 59분
            day_of_week='mon-fri'  # 월-금
        )
    )

    # 일일 데이터 수집 (장 마감 후, 기존 유지)
    scheduler.add_job(day_collect_job, CronTrigger(minute='35', hour='15', day_of_week='0-4'))

    scheduler.start()