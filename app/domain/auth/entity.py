"""
Auth 도메인 엔티티 - ORM 모델 + 비즈니스 로직
"""
from sqlalchemy import Column, Integer, String, CHAR, DateTime, Sequence
from datetime import datetime

from app.common.database import Base
from app.exceptions import ValidationError


class Auth(Base):
    """인증키 엔티티"""
    __tablename__ = "AUTH_KEY"

    AUTH_ID = Column(Integer, Sequence('auth_id_seq'), primary_key=True, comment='권한 ID')
    USER_ID = Column(String(50), nullable=False, primary_key=True, comment='사용자 ID')
    AUTH_NAME = Column(String(50), nullable=False, comment='권한 이름')
    SIMULATION_YN = Column(CHAR(1), default='N', nullable=False, comment='모의 투자 여부')
    API_KEY = Column(String(200), nullable=False, comment='앱키')
    SECRET_KEY = Column(String(350), nullable=False, comment='시크릿 키')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')

    # ==================== 비즈니스 로직 ====================

    def validate(self) -> None:
        """인증키 유효성 검증"""
        if not self.AUTH_NAME:
            raise ValidationError("인증키 이름은 필수입니다")
        if self.SIMULATION_YN not in ('Y', 'N'):
            raise ValidationError("모의투자 여부는 Y 또는 N이어야 합니다")
        if not self.API_KEY:
            raise ValidationError("API 키는 필수입니다")
        if not self.SECRET_KEY:
            raise ValidationError("시크릿 키는 필수입니다")

    # ==================== 팩토리 메서드 ====================

    @classmethod
    def create(cls, user_id: str, auth_name: str, simulation_yn: str,
               api_key: str, secret_key: str) -> "Auth":
        """새 인증키 생성"""
        auth = cls(
            USER_ID=user_id,
            AUTH_NAME=auth_name,
            SIMULATION_YN=simulation_yn,
            API_KEY=api_key,
            SECRET_KEY=secret_key
        )
        auth.validate()
        return auth
