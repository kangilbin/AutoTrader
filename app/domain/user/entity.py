"""
User 도메인 엔티티 - ORM 모델 + 비즈니스 로직
"""
from sqlalchemy import Column, Integer, String, CHAR, DateTime
from datetime import datetime

from app.common.database import Base


class UserIdSequence(Base):
    """USER_ID 자동 생성용 시퀀스 테이블"""
    __tablename__ = "USER_ID_SEQUENCE"

    id = Column(Integer, primary_key=True, autoincrement=True, comment='시퀀스 번호')
    created_at = Column(DateTime, default=datetime.now, comment='생성 시간')


class User(Base):
    """사용자 엔티티"""
    __tablename__ = "USER"

    USER_ID = Column(String(50), primary_key=True, comment='사용자 ID (자동생성: USR00001)')
    USER_NAME = Column(String(50), nullable=False, comment='사용자 이름')
    EMAIL = Column(String(100), nullable=True, unique=True, comment='이메일 주소')
    PHONE = Column(CHAR(11), nullable=True, comment='휴대폰 번호 (OAuth 사용자는 나중에 입력)')
    GOOGLE_ACCESS_TOKEN = Column(String(2000), nullable=True, comment='Google OAuth access token (Gemini용)')
    GOOGLE_REFRESH_TOKEN = Column(String(500), nullable=True, comment='Google OAuth refresh token')
    GOOGLE_TOKEN_EXPIRES_AT = Column(DateTime, nullable=True, comment='Google access token 만료 시점')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')

    @classmethod
    def create_oauth_user(cls, user_id: str, user_name: str, email: str,
                          google_access_token: str, google_refresh_token: str = None,
                          google_token_expires_at: datetime = None) -> "User":
        """Google OAuth 사용자 생성"""
        return cls(
            USER_ID=user_id,
            USER_NAME=user_name,
            EMAIL=email,
            GOOGLE_ACCESS_TOKEN=google_access_token,
            GOOGLE_REFRESH_TOKEN=google_refresh_token,
            GOOGLE_TOKEN_EXPIRES_AT=google_token_expires_at
        )
