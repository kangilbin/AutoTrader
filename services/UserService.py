import json
from datetime import timedelta
from fastapi import HTTPException

from api.KISOpenApi import oauth_token
from crud.UserCrud import insert_user, select_user
from model.schemas.UserModel import UserCreate, UserResponse
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_jwt_auth import AuthJWT
from module.RedisConnection import redis


async def create_user(db: AsyncSession, user_data: UserCreate) -> UserResponse:
    return await insert_user(db, user_data)


async def login_user(db, user_id: str, user_pw: str, authorize: AuthJWT):
    user_info = await select_user(db, user_id, user_pw)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    login_token = authorize.create_access_token(subject=user_id)
    login_refresh_token = authorize.create_refresh_token(subject=user_id)

    await redis().hset(user_id, mapping=user_info, ex=3600, xx=True)
    return login_token, login_refresh_token


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
