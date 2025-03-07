from pydantic import BaseModel


class Signup(BaseModel):
    USER_ID: str
    USER_NAME: str
    PASSWORD: str
    API_KEY: str
    SECRET_KEY: str
    DEVICE_ID: str
