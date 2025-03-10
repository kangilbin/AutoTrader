from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select as sql_select, insert as sql_insert, update as sql_update , delete as sql_delete

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


# Insert
async def insert(self, db: AsyncSession, **kwargs):
    db_obj = self.model(**kwargs)
    db.add(db_obj)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

# Batch Insert (여러 개의 데이터 레코드를 삽입)
async def batch_insert(self, db: AsyncSession, data_list: list):
    # 여러 개의 데이터 레코드를 삽입
    stmt = sql_insert(self.model).values(data_list)
    result = await db.execute(stmt)
    await db.commit()  # 커밋
    return result.rowcount  # 삽입된 행 수 반환


# Read
# query(statement)를 먼저 작성 -> 실행
async def select(self, db: AsyncSession, **filters):
    sql_select(self.model).where(**filters)
    for attr, value in filters.items():
        query = query.filter(getattr(self.model, attr) == value)
    result = await db.execute(query)
    return result.scalar()

async def select_by_query(db: AsyncSession, query: str):
    """ 직접 쿼리문을 작성하여 실행 """
    # text()로 raw SQL을 실행
    result = await db.execute(query)
    return result.fetchall()  # 결과 반환

async def select_list(self, db: AsyncSession, page=None, page_size=None):
    query = sql_select(self.model)
    if page and page_size:
        query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()

# Update
async def update(db: AsyncSession, db_obj, **kwargs):
    for key, value in kwargs.items():
        setattr(db_obj, key, value)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def update2(self, db: AsyncSession, columns: dict, filters: dict):
    filters = [(getattr(self.model, k) == v) for k, v in filters.items()]

    query = sql_update(self.model).where(*filters).values(columns)
    await db.execute(query)
    await db.commit()

# Delete
async def delete(db: AsyncSession, db_obj):
    await db.sql_delete(db_obj)
    await db.commit()
    return db_obj

async def delete2(self, db: AsyncSession, columns: dict, filters: dict):
    filters = [(getattr(self.model, k) == v) for k, v in filters.items()]

    query = update(self.model).where(*filters).values(columns)
    await db.execute(query)
    await db.commit()