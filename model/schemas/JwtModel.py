from datetime import timedelta
from pydantic import BaseModel
from module.Config import get_env


class Settings(BaseModel):
    JWT_SECRET_KEY: str = get_env("JWT_SECRET_KEY")
    TOKEN_ACCESS_EXP: timedelta = timedelta(minutes=15)
    TOKEN_REFRESH_EXP: timedelta = timedelta(days=7)