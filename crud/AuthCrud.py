from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_
from model.orm.AuthKey import Auth
from model.schemas.AuthModel import AuthCreate, AuthResponse


# Auth key 조회
async def select_auth(db: AsyncSession, user_id: str, auth_id: str) -> AuthResponse:
    query = select(Auth).filter(
        and_(Auth.USER_ID == user_id, Auth.AUTH_ID == auth_id)
    )
    result = await db.execute(query)
    return AuthResponse.from_orm(result.scalars().first())


# Auth key 조회(list)
async def list_auth(db: AsyncSession, user_id: str):
    query = select(Auth).filter(Auth.USER_ID == user_id)
    result = await db.execute(query)
    return result.scalars().all()


# Auth key 생성
async def insert_auth(db: AsyncSession, auth_data: AuthCreate) -> AuthResponse:
    # 새로운 사용자 객체 생성
    db_auth = Auth(USER_ID=auth_data.USER_ID, USER_NAME=auth_data.SIMULATION_YN, API_KEY=auth_data.API_KEY,
                   SECRET_KEY=auth_data.SECRET_KEY)
    db.add(db_auth)
    await db.commit()
    await db.refresh(db_auth)
    return AuthResponse.from_orm(db_auth)


# Auth key 업데이트
async def update_auth(db: AsyncSession, auth_data: AuthCreate):
    query = (
        update(Auth)
        .where(Auth.AUTH_ID == auth_data.AUTH_ID)
        .values(**Auth.dict())
        .execution_options(synchronize_session=False)
    )
    await db.execute(query)
    await db.commit()
    # 업데이트 후 db에서 다시 가져오기
    return await db.get(Auth, auth_data.AUTH_ID)


# Auth key 삭제
async def delete_auth(db: AsyncSession, auth_id: str):
    query = delete(Auth).where(Auth.AUTH_ID == auth_id)
    result = await db.execute(query)
    await db.commit()

    if result.rowcount == 0:
        return None  # 삭제된 행이 없으면 None 반환
    return auth_id
