# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from batch.AutoSwingBatch import job


def schedule_start():
    scheduler = AsyncIOScheduler()
    # 주말을 제외하고 월요일부터 금요일까지 9시부터 15시 20분까지 1분 단위로 실행
    scheduler.add_job(job, CronTrigger(minute='0-59', hour='9-14', day_of_week='0-4'))
    scheduler.add_job(job, CronTrigger(minute='0-21', hour='15', day_of_week='0-4'))
    scheduler.start()


