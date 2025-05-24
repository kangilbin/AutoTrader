from fastapi import HTTPException
from fastapi_jwt_auth import AuthJWT
from sqlalchemy.ext.asyncio import AsyncSession
from app.crud.UserCrud import insert_user, select_user, update_user, delete_user
from app.model.schemas.UserModel import UserCreate, UserResponse
from app.module.HashCrypto import hash_password, check_password
from app.module.RedisConnection import get_redis
from datetime import datetime


async def create_user(db: AsyncSession, user_data: UserCreate) -> UserResponse:
    user_data.PASSWORD = hash_password(user_data.PASSWORD)
    return await insert_user(db, user_data)


async def login_user(db, user_id: str, user_pw: str, authorize: AuthJWT):
    user_info = await select_user(db, user_id)

    if not user_info or not check_password(user_pw, user_info["PASSWORD"]):
        raise HTTPException(status_code=401, detail="잘못된 아이디 또는 비밀번호입니다.")

    access_token = authorize.create_access_token(subject=user_id, user_claims={"USER_NAME": user_info["USER_NAME"], "PHONE": user_info["PHONE"]})
    refresh_token = authorize.create_refresh_token(subject=user_id)

    return access_token, refresh_token


async def duplicate_user(db: AsyncSession, user_id: str):
    return await select_user(db, user_id)


async def mod_user(db: AsyncSession, user_data: UserCreate):
    user_data.MOD_DT = datetime.now()
    await update_user(db, user_data)


async def remove_user(db: AsyncSession, user_id: str):
    await delete_user(db, user_id)


async def token_refresh(refresh_token: str, authorize: AuthJWT):
    # refresh token 검증
    try:
        # refresh token 검증
        authorize.jwt_refresh_token_required(token=refresh_token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Refresh token이 만료되었거나 유효하지 않습니다.")

    # 새로운 액세스 토큰 발급
    user_id = authorize.get_jwt_subject()
    user_claims = authorize.get_raw_jwt().get("user_claims", {})
    access_token = authorize.create_access_token(subject=user_id, user_claims=user_claims)

    return access_token

