# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.domain.swing.auto_swing_batch import trade_job, day_collect_job

scheduler = AsyncIOScheduler()


async def schedule_start():
    # 주말을 제외하고 월요일부터 금요일까지 9시부터 15시 20분까지 1분 단위로 실행
    scheduler.add_job(trade_job, CronTrigger(minute='0', hour='9-14', day_of_week='0-4'))
    scheduler.add_job(trade_job, CronTrigger(minute='18', hour='15', day_of_week='0-4'))
    scheduler.add_job(day_collect_job, CronTrigger(minute='31', hour='15', day_of_week='0-4')) # 일일 데이터 수집
    scheduler.start()


