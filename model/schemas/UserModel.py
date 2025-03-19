from pydantic import BaseModel


class UserCreate(BaseModel):
    USER_ID: str
    USER_NAME: str
    PASSWORD: str
    API_KEY: str
    SECRET_KEY: str
    DEVICE_ID: str

class UserResponse(UserCreate):

    class Config:
        orm_mode = True