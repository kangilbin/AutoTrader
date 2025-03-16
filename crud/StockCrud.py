from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_
from model.schemas.UserModel import UserCreate, UserResponse
from model.orm.User import User


# 비동기 사용자 조회
async def select_stock(db: AsyncSession, user_id: str, user_pw: str):
    query = select(User).filter(
        and_(User.USER_ID == user_id, User.PASSWORD == user_pw)
    )
    result = await db.execute(query)
    return result.scalars()



