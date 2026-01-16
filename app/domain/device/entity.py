"""
Device 도메인 엔티티 - 비즈니스 로직 캡슐화
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.exceptions import ValidationError


@dataclass
class Device:
    """디바이스 도메인 엔티티"""
    device_id: str
    device_name: str
    user_id: Optional[str] = None
    active_yn: str = 'Y'
    reg_dt: Optional[datetime] = field(default_factory=datetime.now)
    mod_dt: Optional[datetime] = None

    # ==================== 비즈니스 로직 ====================

    def validate(self) -> None:
        """디바이스 유효성 검증"""
        if not self.device_id:
            raise ValidationError("디바이스 ID는 필수입니다")
        if not self.device_name:
            raise ValidationError("디바이스 이름은 필수입니다")
        if self.active_yn not in ('Y', 'N'):
            raise ValidationError("활성 여부는 Y 또는 N이어야 합니다")

    def is_active(self) -> bool:
        """활성화 여부"""
        return self.active_yn == 'Y'

    def belongs_to_user(self, user_id: str) -> bool:
        """특정 사용자 전용 디바이스인지 확인"""
        return self.user_id is None or self.user_id == user_id

    def activate(self) -> None:
        """디바이스 활성화"""
        self.active_yn = 'Y'
        self.mod_dt = datetime.now()

    def deactivate(self) -> None:
        """디바이스 비활성화"""
        self.active_yn = 'N'
        self.mod_dt = datetime.now()

    def update(self, device_name: str = None, user_id: str = None, active_yn: str = None) -> None:
        """디바이스 정보 수정"""
        if device_name:
            self.device_name = device_name
        if user_id is not None:
            self.user_id = user_id
        if active_yn:
            if active_yn not in ('Y', 'N'):
                raise ValidationError("활성 여부는 Y 또는 N이어야 합니다")
            self.active_yn = active_yn
        self.mod_dt = datetime.now()

    # ==================== 팩토리 메서드 ====================

    @classmethod
    def create(cls, device_id: str, device_name: str, user_id: Optional[str] = None) -> "Device":
        """새 디바이스 생성"""
        device = cls(
            device_id=device_id,
            device_name=device_name,
            user_id=user_id,
            active_yn='Y'
        )
        device.validate()
        return device