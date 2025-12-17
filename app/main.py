"""
FastAPI 애플리케이션 진입점
"""
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.common.database import Database
from app.module.redis_connection import Redis
from app.module.schedules import schedule_start
from app.common.exceptions import AppException
from app.core.response import error_response

# 라우터 임포트
from app.routers import (
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


# 전역 예외 핸들러
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """커스텀 예외 핸들러"""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            message=exc.detail,
            error_code=exc.error_code
        )
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """일반 예외 핸들러"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=error_response(
            message="서버 내부 오류가 발생했습니다",
            error_code="INTERNAL_SERVER_ERROR",
            detail=str(exc) if logger.level == logging.DEBUG else None
        )
    )


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
