"""
데이터베이스 연결 설정

ORM 모델은 각 도메인의 entity.py에 정의되어 있습니다.
- app/domain/user/entity.py: User, UserIdSequence
- app/domain/account/entity.py: Account
- app/domain/auth/entity.py: Auth
- app/domain/stock/entity.py: Stock, StockHistory
- app/domain/swing/entity.py: SwingTrade, EmaOption
- app/domain/trade_history/entity.py: TradeHistory
- app/domain/device/entity.py: Device
- app/domain/notification/entity.py: UserNotiSetting, UserPushToken
"""
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import get_settings
import logging

logger = logging.getLogger(__name__)

Base = declarative_base()


# ==================== Database Connection ====================

class Database:
    """데이터베이스 연결 관리 (싱글톤)"""
    _engine = None
    _async_session = None
    _meta = MetaData()

    @classmethod
    async def connect(cls):
        """DB 엔진과 세션 팩토리 초기화"""
        if cls._engine is None:
            settings = get_settings()
            cls._engine = create_async_engine(
                settings.DATABASE_URL,
                echo=settings.DB_ECHO,
                pool_size=settings.DB_POOL_SIZE,
                max_overflow=settings.DB_MAX_OVERFLOW,
                pool_timeout=settings.DB_POOL_TIMEOUT,
                pool_recycle=settings.DB_POOL_RECYCLE
            )
            logger.info(f"Database engine created (echo={settings.DB_ECHO})")

            # 모든 도메인의 entity를 import하여 Base.metadata에 등록
            _import_all_entities()

            async with cls._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        if cls._async_session is None:
            cls._async_session = sessionmaker(
                bind=cls._engine,
                class_=AsyncSession,
                autoflush=False,
                expire_on_commit=False
            )

    @classmethod
    async def disconnect(cls):
        """DB 엔진 종료"""
        if cls._engine is not None:
            await cls._engine.dispose()
            cls._engine = None
            cls._async_session = None
            logger.info("Database disconnected")

    @classmethod
    async def get_session(cls) -> AsyncSession:
        """비동기 세션 반환"""
        if cls._engine is None or cls._async_session is None:
            await cls.connect()
        return cls._async_session()


def _import_all_entities():
    """모든 도메인 Entity를 import하여 Base.metadata에 등록"""
    import app.domain.user.entity  # noqa: F401
    import app.domain.account.entity  # noqa: F401
    import app.domain.auth.entity  # noqa: F401
    import app.domain.stock.entity  # noqa: F401
    import app.domain.swing.entity  # noqa: F401
    import app.domain.trade_history.entity  # noqa: F401
    import app.domain.device.entity  # noqa: F401
    import app.domain.notification.entity  # noqa: F401


async def get_db():
    """의존성 주입용 DB 세션"""
    db = await Database.get_session()
    try:
        yield db
    finally:
        await db.close()
