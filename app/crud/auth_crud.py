from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_
from app.model.table_create import Auth
from app.model.schemas.auth_model import AuthCreate, AuthResponse
import logging
from sqlalchemy.exc import SQLAlchemyError


# Auth key 조회
async def select_auth(db: AsyncSession, user_id: str, auth_id: int):
    try:
        query = select(Auth).filter(
            and_(Auth.USER_ID == user_id, Auth.AUTH_ID == auth_id)
        )
        result = await db.execute(query)
    except SQLAlchemyError as e:
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise
    return AuthResponse.model_validate(result.scalars().first()).model_dump()


# Auth key 조회(list)
async def list_auth(db: AsyncSession, user_id: str):
    query = select(Auth).filter(Auth.USER_ID == user_id)
    db_auth = await db.execute(query)
    return db_auth.scalars().all()


# Auth key 생성
async def insert_auth(db: AsyncSession, auth_data: AuthCreate,) :
    # 새로운 사용자 객체 생성
    try:
        db_auth = Auth(USER_ID=auth_data.USER_ID, AUTH_NAME=auth_data.AUTH_NAME, SIMULATION_YN=auth_data.SIMULATION_YN,
                       API_KEY=auth_data.API_KEY, SECRET_KEY=auth_data.SECRET_KEY)
        db.add(db_auth)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise e

    await db.refresh(db_auth)
    return AuthResponse.model_validate(db_auth).model_dump()


# Auth key 업데이트
async def update_auth(db: AsyncSession, auth_data: AuthCreate):
    try:
        query = (
            update(Auth)
            .filter(Auth.AUTH_ID == auth_data.AUTH_ID)
            .values(**Auth.dict())
            .execution_options(synchronize_session=False)
        )
        await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise e
    # 업데이트 후 db에서 다시 가져오기
    return await db.get(Auth, auth_data.AUTH_ID)


# Auth key 삭제
async def delete_auth(db: AsyncSession, auth_id: str):
    try:
        query = delete(Auth).filter(Auth.AUTH_ID == auth_id)
        result = await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise e

    if result.rowcount == 0:
        return None  # 삭제된 행이 없으면 None 반환
    return auth_id
