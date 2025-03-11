from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

meta = MetaData()
engine = create_async_engine(
    "mysql+asyncmy://root:qwer1234!@localhost/AUTO_TRADER",
    echo=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800
)
async_session = async_sessionmaker(engine, autoflush=False, autocommit=False)

async def get_db() -> AsyncSession:
    # 비동기에서 달라지는 부분
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    db = async_session()
    try:
        yield db
    finally:
        await db.close()

