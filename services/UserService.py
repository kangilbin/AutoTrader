from fastapi import HTTPException
from fastapi_jwt_auth import AuthJWT
from sqlalchemy.ext.asyncio import AsyncSession
from crud.UserCrud import insert_user, select_user, update_user, delete_user
from model.schemas.UserModel import UserCreate, UserResponse
from module.HashCrypto import hash_password, check_password
from module.RedisConnection import get_redis
from datetime import datetime


async def create_user(db: AsyncSession, user_data: UserCreate) -> UserResponse:
    user_data.PASSWORD = hash_password(user_data.PASSWORD)
    return await insert_user(db, user_data)


async def login_user(db, user_id: str, user_pw: str, user_dvc: str, authorize: AuthJWT):
    user_info = await select_user(db, user_id, user_dvc)
    if not user_info or not check_password(user_pw, user_info.PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    login_token = authorize.create_access_token(subject=user_id)
    login_refresh_token = authorize.create_refresh_token(subject=user_id)

    await get_redis().hset(user_id, mapping=user_info, ex=3600, xx=True)
    return login_token, login_refresh_token


async def mod_user(db: AsyncSession, user_data: UserCreate):
    user_data.MOD_DT = datetime.now()
    await update_user(db, user_data)


async def remove_user(db: AsyncSession, user_id: str):
    await delete_user(db, user_id)


async def refresh_token(authorize: AuthJWT):
    # refresh token 검증
    try:
        # refresh token 검증
        authorize.jwt_refresh_token_required()
    except Exception as e:
        raise HTTPException(status_code=401, detail="Refresh token expired or invalid")

    # 새로운 액세스 토큰 발급
    user_id = authorize.get_jwt_subject()
    login_token = authorize.create_access_token(subject=user_id)

    return login_token
