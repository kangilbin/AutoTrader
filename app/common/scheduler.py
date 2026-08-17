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
from app.domain.stock.price_adjustment_batch import price_adjustment_reload_job
from app.domain.stock.stock_data_batch import collect_job_cron
from app.core.config import get_settings

settings = get_settings()

# 기본 타임존을 KST로 명시 → 국내 잡(timezone 미지정)이 KST에 확정 앵커됨.
# 컨테이너 런타임 TZ(UTC 등)에 의존하지 않도록 방어.
# 미국 잡은 CronTrigger(timezone=us_tz)가 트리거 단위로 오버라이드하므로 무관.
scheduler = AsyncIOScheduler(timezone='Asia/Seoul')


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
    # 실행 시각은 일봉 확정 시각(stock_data_batch)에서 유도 — 같은 시각에 걸면
    # 잡이 조금만 일찍 깨도 전 종목이 스킵되어 하루치가 유실된다
    scheduler.add_job(
        day_collect_job,
        CronTrigger(day_of_week='mon-fri', **collect_job_cron(overseas=False))
    )

    # 수정주가 재적재: 평일 새벽 02:30 KST
    # KSD 액면교체/합병/분할 일정 조회 → 효력일 도래 종목 3년치 재적재
    scheduler.add_job(
        price_adjustment_reload_job,
        CronTrigger(minute='30', hour='2', day_of_week='mon-fri')
    )

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

    # 해외 스윙 매매 배치: 기본 10:00-15:55 ET (월~금), 5분마다
    # 주기/범위는 .env로 덮어쓸 수 있다 (테스트: US_TRADE_CRON_MINUTE="*/1", US_TRADE_CRON_HOUR="1-23")
    # 범위를 넓혀도 us_trade_job 진입점의 정규장 가드가 09:30-16:00 ET 밖 실행을 막는다.
    # (장 시간 밖 테스트는 ALLOW_OFFHOURS_TRADING=true 필요)
    scheduler.add_job(
        us_trade_job,
        CronTrigger(
            minute=settings.US_TRADE_CRON_MINUTE,
            hour=settings.US_TRADE_CRON_HOUR,
            day_of_week='mon-fri',
            timezone=us_tz
        )
    )

    # 미국 일일 데이터 수집 (미국장 마감 후)
    scheduler.add_job(
        us_day_collect_job,
        CronTrigger(day_of_week='mon-fri', **collect_job_cron(overseas=True))
    )

    scheduler.start()
