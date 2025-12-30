# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.domain.swing.auto_swing_batch import trade_job, day_collect_job

scheduler = AsyncIOScheduler()


async def schedule_start():
    # 스윙 트레이딩 배치 작업: 평일 9시-15시, 5분마다 실행
    # 기존: 1시간 간격 (~7회/일) → 신규: 5분 간격 (~74회/일)
    scheduler.add_job(
        trade_job,
        CronTrigger(
            minute='*/5',      # 5분마다 (0,5,10,15,20,25,30,35,40,45,50,55)
            hour='9-14',       # 9시-14시 59분
            day_of_week='mon-fri'  # 월-금
        )
    )

    # 15시 00분, 05분 추가 실행 (장 마감 직전/직후)
    scheduler.add_job(
        trade_job,
        CronTrigger(minute='0,5', hour='15', day_of_week='mon-fri')
    )

    # 일일 데이터 수집 (장 마감 후, 기존 유지)
    scheduler.add_job(day_collect_job, CronTrigger(minute='31', hour='15', day_of_week='0-4'))

    scheduler.start()


