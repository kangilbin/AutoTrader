from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete

from model.schemas import UserModel
from model.orm import User


# 비동기 사용자 조회
async def get_user(db: AsyncSession, user: User, user_id: str):
    # select 문을 사용하여 비동기적으로 데이터를 조회
    query = select(user).filter(user.id == user_id)
    result = await db.execute(query)
    return result.scalars()


# 사용자 생성
async def create_user(db: AsyncSession, user: User, user_data: UserModel):
    # 새로운 사용자 객체 생성
    db_user = user(USER_ID=user_data.USER_ID, USER_NAME=user_data.USER_NAME, PASSWORD=user_data.PASSWORD, DEVICE_ID=user_data.DEVICE_ID
                   , API_KEY=user_data.API_KEY, SECRET_KEY=user_data.SECRET_KEY)
    db.add(db_user)  # 세션에 추가
    await db.commit()  # 비동기 커밋
    await db.refresh(db_user)  # 새로 추가된 사용자 객체 리프레시
    return db_user

# 사용자 업데이트
async def update_user(db: AsyncSession, user: User, user_id: str, user_data: UserModel):
    query = (
        update(user)
        .where(user.USER_ID == user_id)
        .values(**user_data.dict())
        .execution_options(synchronize_session="fetch")
    )
    await db.execute(query)
    await db.commit()
    return await db.get(user, user_id)


# 사용자 삭제
async def delete_user(db: AsyncSession, user_id: str):
    query = select(User).filter(User.id == user_id)
    result = await db.execute(query)
    db_user = result.scalar()
    if db_user:
        await db.delete(db_user)
        await db.commit()
    return db_user