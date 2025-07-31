from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.crud.UserCrud import insert_user, select_user, update_user, delete_user
from app.model.schemas.UserModel import UserCreate, UserResponse
from app.module.HashCrypto import hash_password, check_password
from app.module.RedisConnection import get_redis
from datetime import datetime
from app.module.JwtUtils import create_access_token, create_refresh_token, verify_token, settings

async def create_user(db: AsyncSession, user_data: UserCreate) -> UserResponse:
    user_data.PASSWORD = hash_password(user_data.PASSWORD)
    return await insert_user(db, user_data)


async def login_user(db, user_id: str, user_pw: str):
    user_info = await select_user(db, user_id)

    if not user_info or not check_password(user_pw, user_info["PASSWORD"]):
        raise HTTPException(status_code=401, detail="잘못된 아이디 또는 비밀번호입니다.")


    access_token = create_access_token(user_id, user_info={"USER_NAME": user_info["USER_NAME"], "PHONE": user_info["PHONE"]})
    refresh_token = create_refresh_token(user_id)
    

    # 리프레시 토큰 만료 시간 설정
    redis = await get_redis()
    await redis.hset(user_id, mapping={"refresh_token": refresh_token, "USER_NAME": user_info["USER_NAME"], "PHONE": user_info["PHONE"]})
    await redis.expire(user_id, settings.token_refresh_exp)

    return access_token, refresh_token


async def duplicate_user(db: AsyncSession, user_id: str):
    return await select_user(db, user_id)


async def mod_user(db: AsyncSession, user_data: UserCreate):
    user_data.MOD_DT = datetime.now(datetime.UTC)
    await update_user(db, user_data)


async def remove_user(db: AsyncSession, user_id: str):
    await delete_user(db, user_id)


async def token_refresh(refresh_token: str):
    # refresh token 검증
    token_data = verify_token(refresh_token)
    if token_data is None:
        raise HTTPException(status_code=401, detail="Refresh token이 만료되었거나 유효하지 않습니다.")
    
    user_id = token_data.user_id
    redis = await get_redis()
    
    # Redis에서 저장된 사용자 정보를 한 번에 가져와서 검증
    user_info = await redis.hgetall(user_id)
    if not user_info or user_info.get("refresh_token") != refresh_token:
        raise HTTPException(status_code=401, detail="유효하지 않은 리프레시 토큰입니다.")
    
    # 새로운 액세스 토큰 발급
    access_token = create_access_token(user_id, user_info={"USER_NAME": user_info.get("USER_NAME"), "PHONE": user_info.get("PHONE")})

    return access_token

