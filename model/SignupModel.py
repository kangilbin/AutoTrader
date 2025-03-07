from pydantic import BaseModel


class Signup(BaseModel):
    ID: str
    PASSWORD: str
    API_KEY: str
    SECRET_KEY: str
    DEVICE_ID: str
