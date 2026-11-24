"""
Device 도메인 엔티티 - ORM 모델 + 비즈니스 로직
"""
from sqlalchemy import Column, String, CHAR, DateTime
from datetime import datetime

from app.common.database import Base


class Device(Base):
    """디바이스 화이트리스트 엔티티"""
    __tablename__ = "DEVICE"

    DEVICE_ID = Column(String(100), primary_key=True, comment='디바이스 ID')
    DEVICE_NAME = Column(String(100), nullable=False, comment='디바이스 이름')
    USER_ID = Column(String(50), nullable=True, comment='사용자 ID (NULL=공용)')
    ACTIVE_YN = Column(CHAR(1), default='Y', nullable=False, comment='활성 여부')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')
