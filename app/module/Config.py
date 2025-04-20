from dotenv import load_dotenv
import os

load_dotenv(dotenv_path=".env")


def get_env(key: str) -> str:
    value = os.getenv(key)
    if value is None:
        raise ValueError(f"Environment variable {key} not set")
    return value