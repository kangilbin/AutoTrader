from fastapi.middleware.base import BaseHTTPMiddleware
from datetime import timedelta
from pydantic import BaseModel


class Settings(BaseModel):
    JWT_SECRET_KEY: str = "KANG&HONH20240921"
    TOKEN_ACCESS_EXP: timedelta = timedelta(minutes=15)
    TOKEN_REFRESH_EXP: timedelta = timedelta(days=7)