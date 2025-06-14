from datetime import datetime, timedelta
from pydantic import BaseModel
from app.module.Config import get_env


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: str | None = None


class Settings(BaseModel):
    jwt_secret_key: str = get_env("JWT_SECRET_KEY")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7