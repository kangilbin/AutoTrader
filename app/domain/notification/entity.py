"""
Notification 도메인 엔티티 - ORM 모델
"""
from sqlalchemy import Column, Integer, String, CHAR, DateTime, Sequence, UniqueConstraint
from datetime import datetime

from app.common.database import Base


class UserNotiSetting(Base):
    """사용자 알림 설정 엔티티 (행 기반 - 알림 유형별 1행)"""
    __tablename__ = "USER_NOTI_SETTING"

    USER_ID = Column(String(50), primary_key=True, comment='사용자 ID')
    NOTI_TYPE = Column(String(20), primary_key=True, comment='알림 유형 (BUY, SELL, SIGNAL 등)')
    USE_YN = Column(CHAR(1), nullable=False, default='N', comment='사용 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')


class UserPushToken(Base):
    """사용자 푸쉬 토큰 엔티티"""
    __tablename__ = "USER_PUSH_TOKEN"
    __table_args__ = (
        UniqueConstraint('USER_ID', 'PUSH_TOKEN', name='uq_user_push_token'),
    )

    TOKEN_ID = Column(Integer, Sequence('push_token_id_seq'), primary_key=True, comment='토큰 ID')
    USER_ID = Column(String(50), nullable=False, comment='사용자 ID')
    PUSH_TOKEN = Column(String(200), nullable=False, comment='Expo Push Token')
    DEVICE_TYPE = Column(String(20), nullable=True, comment='ios / android')
    ACTIVE_YN = Column(CHAR(1), nullable=False, default='Y', comment='활성 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')