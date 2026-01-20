"""
FastAPI 애플리케이션 진입점
"""
import logging
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.common.database import Database
from app.common.redis import Redis
# from app.common.scheduler import schedule_start
from app.common.middleware import DeviceAuthMiddleware
from app.exceptions.handlers import register_exception_handlers
from app.domain.swing.service import SwingService

# 라우터 임포트
from app.domain.routers import (
    user_router,
    auth_router,
    account_router,
    stock_router,
    order_router,
    swing_router,
    # device_router,
    backtest_router,
    health_router,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 라이프사이클 관리"""
    logger.info("Starting AutoTrader API...")

    # 시작 시 리소스 초기화
    await Database.connect()
    await Redis.connect()

    # EMA 캐시 워밍업 (애플리케이션 시작 시 1회 실행)
    await _warmup_ema_cache()

    # await schedule_start()

    logger.info("AutoTrader API started successfully")

    try:
        yield
    finally:
        # 종료 시 리소스 정리
        logger.info("Shutting down AutoTrader API...")
        await Database.disconnect()
        await Redis.disconnect()
        logger.info("AutoTrader API shutdown complete")


async def _warmup_ema_cache():
    """EMA 캐시 워밍업 (시작 시 1회)"""
    db = None
    try:
        db = await Database.get_session()
        redis_client = await Redis.get_connection()
        swing_service = SwingService(db)

        result = await swing_service.warmup_ema_cache(redis_client)
        logger.info(f"EMA 캐시 워밍업 완료: {result}")

    except Exception as e:
        logger.warning(f"EMA 캐시 워밍업 실패 (서비스는 계속 실행): {e}")
    finally:
        if db:
            await db.close()


app = FastAPI(
    title="AutoTrader API",
    description="한국 주식 자동매매 백엔드 서비스",
    version="1.0.0",
    lifespan=lifespan
)

# 전역 예외 핸들러 등록
register_exception_handlers(app)

# 디바이스 인증 미들웨어 등록 (모든 요청에 적용)
app.add_middleware(DeviceAuthMiddleware)

# 라우터 등록
app.include_router(health_router)
app.include_router(user_router)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(stock_router)
app.include_router(order_router)
app.include_router(swing_router)
# app.include_router(device_router)
app.include_router(backtest_router)


@app.get("/", tags=["Root"])
async def root():
    """API 루트"""
    return {
        "message": "Welcome to AutoTrader API",
        "docs": "/docs",
        "redoc": "/redoc"
    }
