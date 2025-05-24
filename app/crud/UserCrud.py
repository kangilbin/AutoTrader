from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_
from app.model.TableCreate import User
from app.model.schemas.UserModel import UserCreate, UserResponse
from sqlalchemy.exc import SQLAlchemyError
import logging


# 비동기 사용자 조회
async def select_user(db: AsyncSession, user_id: str):
    query = select(User).filter(
        and_(User.USER_ID == user_id)
    )
    result = await db.execute(query)
    db_user = result.scalars().first()
    if not db_user:
        return None
    return UserResponse.from_orm(db_user).to_dict()


# 사용자 생성
async def insert_user(db: AsyncSession, user_data: UserCreate):
    # 새로운 사용자 객체 생성
    try:
        db_user = User(USER_ID=user_data.USER_ID, USER_NAME=user_data.USER_NAME, PHONE=user_data.PHONE, PASSWORD=user_data.PASSWORD)
        db.add(db_user)  # 세션에 추가
        await db.commit()  # 비동기 커밋
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise

    await db.refresh(db_user)  # 새로 추가된 사용자 객체 리프레시
    return UserResponse.from_orm(db_user).to_dict()


# 사용자 업데이트
async def update_user(db: AsyncSession, user_data: UserCreate):
    try:
        query = (
            update(User)
            .filter(User.USER_ID == user_data.USER_ID)
            .values(**user_data.dict())
            .execution_options(synchronize_session=False)
        )
        await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise

    # 업데이트 후 db에서 다시 가져오기
    return await db.get(User, user_data.USER_ID)


# 사용자 삭제
async def delete_user(db: AsyncSession, user_id: str):
    try:
        query = delete(User).filter(User.USER_ID == user_id)
        result = await db.execute(query)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Database error occurred: {e}", exc_info=True)
        raise

    if result.rowcount == 0:
        return None  # 삭제된 행이 없으면 None 반환
    return user_id
