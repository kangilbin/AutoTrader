"""
API 라우터 모듈
각 도메인에서 라우터를 가져옴
"""
from app.domain.user.router import router as user_router
from app.domain.auth.router import router as auth_router
from app.domain.account.router import router as account_router
from app.domain.stock.router import router as stock_router
from app.domain.order.router import router as order_router
from app.domain.swing.router import router as swing_router
from app.domain.routers.backtest_router import router as backtest_router
from app.domain.routers.health_router import router as health_router

__all__ = [
    "user_router",
    "auth_router",
    "account_router",
    "stock_router",
    "order_router",
    "swing_router",
    "backtest_router",
    "health_router",
]
