from dotenv import load_dotenv
import os

load_dotenv(dotenv_path=".env")


def get_env(key: str) -> str:
    value = os.getenv(key)
    if value is None:
        raise ValueError(f"환경 변수 {key}가 설정되지 않았습니다")
    return value