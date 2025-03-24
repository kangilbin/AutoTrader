from datetime import timedelta
from pydantic import BaseModel
from module.Config import get_env


class Settings(BaseModel):
    authjwt_secret_key: str = get_env("JWT_SECRET_KEY")
    token_access_exp: timedelta = timedelta(minutes=15)
    token_refresh_exp: timedelta = timedelta(days=7)