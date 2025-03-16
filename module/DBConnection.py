from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

meta = MetaData()
engine = create_async_engine(
    "mysql+asyncmy://kang:qwer1234!@localhost:3306/AUTO_TRADER",
    echo=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800
)

# 비동기 세션 생성
async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False
)


async def get_db() -> AsyncSession:
    # 비동기에서 달라지는 부분
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    db = async_session()
    try:
        yield db
    finally:
        await db.close()

