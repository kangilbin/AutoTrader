from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from model.TableCreate import Base
import os


class Database:
    _engine = None
    _async_session = None
    _meta = MetaData()

    @classmethod
    async def connect(cls):
        """DB 엔진과 세션 팩토리를 싱글톤으로 초기화"""
        if cls._engine is None:
            cls._engine = create_async_engine(
                os.getenv("DATABASE_URL"),
                echo=True,
                pool_size=10,
                max_overflow=20,
                pool_timeout=30,
                pool_recycle=1800
            )
            # 테이블 생성 (최초 실행 시)
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

    @classmethod
    async def get_session(cls) -> AsyncSession:
        """비동기 세션 반환 (필요 시 초기화)"""
        if cls._engine is None or cls._async_session is None:
            await cls.connect()
        return cls._async_session()


# 의존성 주입용 함수
async def get_db():
    db = await Database.get_session()
    try:
        yield db
    finally:
        await db.close()
