"""
Account 도메인 엔티티 - ORM 모델 + 비즈니스 로직
"""
from sqlalchemy import Column, Integer, String, DateTime, Sequence
from datetime import datetime

from app.common.database import Base


class Account(Base):
    """계좌 엔티티"""
    __tablename__ = "ACCOUNT"

    ACCOUNT_ID = Column(Integer, Sequence('account_id_seq'), primary_key=True, comment='ACCOUNT ID')
    USER_ID = Column(String(50), nullable=False, primary_key=True, comment='사용자 ID')
    ACCOUNT_NO = Column(String(10), nullable=False, comment='계좌 번호')
    AUTH_ID = Column(Integer, nullable=False, comment='권한 ID')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')

    @classmethod
    def create(cls, user_id: str, account_no: str, auth_id: int) -> "Account":
        """새 계좌 생성"""
        return cls(
            USER_ID=user_id,
            ACCOUNT_NO=account_no,
            AUTH_ID=auth_id
        )
