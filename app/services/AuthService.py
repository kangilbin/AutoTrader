import json
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.KISOpenApi import oauth_token
from app.crud.AuthCrud import insert_auth, select_auth, delete_auth, list_auth, update_auth
from app.model.schemas.AuthModel import AuthCreate, AuthResponse
from app.module.AESCrypto import encrypt, decrypt
from datetime import datetime
from app.module.RedisConnection import get_redis


async def create_auth(db: AsyncSession, auth_data: AuthCreate) -> AuthResponse:
    auth_data.SECRET_KEY = encrypt(auth_data.SECRET_KEY)
    auth_data.API_KEY = encrypt(auth_data.API_KEY)
    return await insert_auth(db, auth_data)


async def get_auth_key(db: AsyncSession, user_id: str, auth_id: int, account_no: str):
    redis = await get_redis()
    await redis.hset(user_id, "ACCOUNT_NO", account_no)

    auth_data = await select_auth(db, user_id, auth_id)
    await oauth_token(user_id, auth_data["SIMULATION_YN"], decrypt(auth_data["API_KEY"]), decrypt(auth_data["SECRET_KEY"]))

    return auth_data


async def get_auth_keys(db: AsyncSession, user_id: str):
    return await list_auth(db, user_id)


async def mod_auth_key(db: AsyncSession, auth_data: AuthCreate):
    auth_data.MOD_DT = datetime.now(datetime.UTC)
    await update_auth(db, auth_data)


async def remove_auth_key(db: AsyncSession, auth_id: str):
    await delete_auth(db, auth_id)


