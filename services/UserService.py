import json
from datetime import timedelta
from fastapi import HTTPException
from crud.User_crud import insert_user, select_user
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

    access_token = authorize.create_access_token(subject=user_id)
    refresh_token = authorize.create_refresh_token(subject=user_id)

    user_info.put("refresh_token", refresh_token)

    await redis().hset(user_id, mapping=user_info, ex=timedelta(days=7))

    return access_token, refresh_token, user_info


async def refresh_token(token: str, authorize: AuthJWT):
    # Authorization 헤더에서 리프레시 토큰 추출

    if not token:
        raise HTTPException(status_code=401, detail="Refresh token missing")

    # 액세스 토큰에서 사용자 ID 추출
    user_id = authorize.get_jwt_subject()

    # 리프레시 토큰이 저장되어 있는지 확인
    user_info = json.loads(await redis().get(user_id))
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if not (user_info.get("refresh_token") and user_info.get("refresh_token") == token):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # 리프레시 토큰을 사용해 새로운 액세스 토큰 발급
    access_token = authorize.create_access_token(subject=user_id)

    return {"access_token": access_token}