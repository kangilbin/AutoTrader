from pydantic import BaseModel


class AuthCreate(BaseModel):
    AUTH_ID: str
    USER_ID: str
    SIMULATION_YN: str
    API_KEY: str
    SECRET_KEY: str
    REG_DT: str
    MOD_DT: str

class AuthResponse(AuthCreate):

    class Config:
        orm_mode = True
