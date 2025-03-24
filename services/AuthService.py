
from sqlalchemy.ext.asyncio import AsyncSession
from crud.AuthCrud import insert_auth, select_auth, delete_auth, list_auth, update_auth
from model.schemas.AuthModel import AuthCreate, AuthResponse
from module.AESCrypto import encrypt
from datetime import datetime


async def create_auth(db: AsyncSession, auth_data: AuthCreate) -> AuthResponse:
    auth_data.SECRET_KEY = encrypt(auth_data.SECRET_KEY)
    auth_data.API_KEY = encrypt(auth_data.API_KEY)
    return await insert_auth(db, auth_data)


async def get_auth_key(db: AsyncSession, user_id: str, auth_id: str):
    return await select_auth(db, user_id, auth_id)


async def get_auth_keys(db: AsyncSession, user_id: str):
    return await list_auth(db, user_id)


async def mod_auth(db: AsyncSession, auth_data: AuthCreate):
    auth_data.MOD_DT = datetime.now()
    await update_auth(db, auth_data)


async def remove_auth(db: AsyncSession, auth_id: str):
    await delete_auth(db, auth_id)


