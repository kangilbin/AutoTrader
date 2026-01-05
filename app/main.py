"""
FastAPI 애플리케이션 진입점
"""
import logging
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.common.database import Database
from app.common.redis import Redis
from app.common.scheduler import schedule_start
from app.exceptions.handlers import register_exception_handlers

# 라우터 임포트
from app.domain.routers import (
    user_router,
    auth_router,
    account_router,
    stock_router,
    order_router,
    swing_router,
    backtest_router,
    health_router,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 라이프사이클 관리"""
    logger.info("Starting AutoTrader API...")

    # 시작 시 리소스 초기화
    await Database.connect()
    await Redis.connect()
    await schedule_start()

    logger.info("AutoTrader API started successfully")

    try:
        yield
    finally:
        # 종료 시 리소스 정리
        logger.info("Shutting down AutoTrader API...")
        await Database.disconnect()
        await Redis.disconnect()
        logger.info("AutoTrader API shutdown complete")


app = FastAPI(
    title="AutoTrader API",
    description="한국 주식 자동매매 백엔드 서비스",
    version="1.0.0",
    lifespan=lifespan
)

# 전역 예외 핸들러 등록
register_exception_handlers(app)

# 라우터 등록
app.include_router(health_router)
app.include_router(user_router)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(stock_router)
app.include_router(order_router)
app.include_router(swing_router)
app.include_router(backtest_router)


@app.get("/", tags=["Root"])
async def root():
    """API 루트"""
    return {
        "message": "Welcome to AutoTrader API",
        "docs": "/docs",
        "redoc": "/redoc"
    }
