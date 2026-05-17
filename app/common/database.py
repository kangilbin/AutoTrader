"""
데이터베이스 연결 및 ORM 모델 정의
"""
from sqlalchemy import MetaData, Integer, Sequence, CHAR, Column, String, DateTime, DECIMAL
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
from app.core.config import get_settings
import logging

logger = logging.getLogger(__name__)

Base = declarative_base()


# ==================== ORM Models ====================

class UserModel(Base):
    """사용자 테이블"""
    __tablename__ = "USER"

    USER_ID = Column(String(50), primary_key=True, comment='사용자 ID')
    USER_NAME = Column(String(50), nullable=False, comment='사용자 이름')
    PHONE = Column(CHAR(11), nullable=False, comment='휴대폰 번호')
    PASSWORD = Column(String(100), nullable=False, comment='비밀 번호')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class AccountModel(Base):
    """계좌 테이블"""
    __tablename__ = "ACCOUNT"

    ACCOUNT_ID = Column(Integer, Sequence('account_id_seq'), primary_key=True, comment='ACCOUNT ID')
    USER_ID = Column(String(50), nullable=False, primary_key=True, comment='사용자 ID')
    ACCOUNT_NO = Column(String(10), nullable=False, comment='계좌 번호')
    AUTH_ID = Column(Integer, nullable=False, comment='권한 ID')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class AuthModel(Base):
    """인증키 테이블"""
    __tablename__ = "AUTH_KEY"

    AUTH_ID = Column(Integer, Sequence('auth_id_seq'), primary_key=True, comment='권한 ID')
    USER_ID = Column(String(50), nullable=False, primary_key=True, comment='사용자 ID')
    AUTH_NAME = Column(String(50), nullable=False, comment='권한 이름')
    SIMULATION_YN = Column(CHAR(1), default='N', nullable=False, comment='모의 투자 여부')
    API_KEY = Column(String(200), nullable=False, comment='앱키')
    SECRET_KEY = Column(String(350), nullable=False, comment='시크릿 키')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class StockModel(Base):
    """종목 정보 테이블"""
    __tablename__ = "STOCK_INFO"

    MARKET_TYPE = Column(String(50), nullable=False, comment='종목 코드', primary_key=True)
    ST_CODE = Column(String(50), nullable=False, comment='종목 코드', primary_key=True)
    SD_CODE = Column(String(50), nullable=False, comment='주식 표준 코드')
    ST_NM = Column(String(100), nullable=False, comment='종목명')
    DATA_YN = Column(CHAR(1), nullable=False, default='N', comment='데이터 적재 여부')
    DEL_YN = Column(CHAR(1), nullable=False, default='N', comment='상장 폐지 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class StockHistoryModel(Base):
    """주식 일별 데이터 테이블"""
    __tablename__ = "STOCK_DAY_HISTORY"

    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    STCK_BSOP_DATE = Column(String(8), nullable=False, primary_key=True, comment='주식 영업 일자')
    STCK_OPRC = Column(DECIMAL(15, 2), nullable=False, comment='주식 시가')
    STCK_HGPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 최고가')
    STCK_LWPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 최저가')
    STCK_CLPR = Column(DECIMAL(15, 2), nullable=False, comment='주식 종가')
    ACML_VOL = Column(Integer, nullable=False, comment='누적 거래량')
    FRGN_NTBY_QTY = Column(Integer, nullable=True, comment='외국인 순매수 수량')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class SwingModel(Base):
    """스윙 매매 테이블"""
    __tablename__ = "SWING_TRADE"

    SWING_ID = Column(Integer, Sequence('swing_id_seq'), primary_key=True, comment='스윙 ID')
    ACCOUNT_NO = Column(String(50), nullable=False, primary_key=True, comment='계좌 번호')
    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    USE_YN = Column(CHAR(1), nullable=False, default='Y', comment='사용 여부')
    INIT_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='초기 투자금')
    CUR_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='현재 투자금')
    SWING_TYPE = Column(CHAR(1), nullable=False, comment='스윙 타입 (A: 이평선, B: 일목균형표)')
    BUY_RATIO = Column(Integer, nullable=False, comment='매수 비율')
    SELL_RATIO = Column(Integer, nullable=False, comment='매도 비율')
    SIGNAL = Column(Integer, nullable=False, default=0, comment='매매 신호 상태 (0:대기, 1:1차매수, 2:2차매수, 3:장중손절, 4:1차매도대기, 5:2차매도대기)')
    ENTRY_PRICE = Column(DECIMAL(15, 2), nullable=True, comment='평균 매수 단가')
    HOLD_QTY = Column(Integer, nullable=True, default=0, comment='보유 수량')
    EOD_SIGNALS = Column(String(500), nullable=True, comment='EOD 매도 신호 JSON {"ema_breach":"2026-01-20","trend_weak":"2026-01-21","supply_weak":"2026-01-22"}')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class EmaOptModel(Base):
    """이평선 옵션 테이블"""
    __tablename__ = "EMA_OPT"

    ACCOUNT_NO = Column(String(50), nullable=False, primary_key=True, comment='계좌 번호')
    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    SHORT_TERM = Column(Integer, nullable=False, comment='단기 이평선')
    MEDIUM_TERM = Column(Integer, nullable=False, comment='중기 이평선')
    LONG_TERM = Column(Integer, nullable=False, comment='장기 이평선')


class TradeHistoryModel(Base):
    """거래 내역 테이블"""
    __tablename__ = "TRADE_HISTORY"

    TRADE_ID = Column(Integer, Sequence('trade_id_seq'), primary_key=True, comment='거래 ID')
    SWING_ID = Column(Integer, nullable=False, comment='스윙 ID')
    TRADE_DATE = Column(DateTime, nullable=False, comment='거래 일자')
    TRADE_TYPE = Column(CHAR(1), nullable=False, comment='거래 타입 (B: 매수, S: 매도)')
    TRADE_PRICE = Column(DECIMAL(15, 2), nullable=False, comment='거래 가격')
    TRADE_QTY = Column(Integer, nullable=False, comment='거래 수량')
    TRADE_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='거래 금액')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')


class DeviceModel(Base):
    """디바이스 화이트리스트 테이블"""
    __tablename__ = "DEVICE"

    DEVICE_ID = Column(String(100), primary_key=True, comment='디바이스 ID')
    DEVICE_NAME = Column(String(100), nullable=False, comment='디바이스 이름')
    USER_ID = Column(String(50), nullable=True, comment='사용자 ID (NULL=공용)')
    ACTIVE_YN = Column(CHAR(1), default='Y', nullable=False, comment='활성 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


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


async def get_db():
    """의존성 주입용 DB 세션"""
    db = await Database.get_session()
    try:
        yield db
    finally:
        await db.close()