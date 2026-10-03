"""
User 도메인 엔티티 - 비즈니스 로직 캡슐화
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.exceptions import ValidationError


@dataclass
class User:
    """사용자 도메인 엔티티"""
    user_id: str
    user_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    google_access_token: Optional[str] = None
    google_refresh_token: Optional[str] = None
    google_token_expires_at: Optional[datetime] = None
    reg_dt: Optional[datetime] = field(default_factory=datetime.now)
    mod_dt: Optional[datetime] = None

    # ==================== 비즈니스 로직 ====================

    def update_profile(self, name: Optional[str] = None, phone: Optional[str] = None) -> None:
        """프로필 수정"""
        if name:
            if len(name) < 2:
                raise ValidationError("이름은 2자 이상이어야 합니다")
            self.user_name = name
        if phone is not None:
            if len(phone) != 11 or not phone.isdigit():
                raise ValidationError("휴대폰 번호는 11자리 숫자여야 합니다")
            self.phone = phone
        self.mod_dt = datetime.now()

    def validate_phone(self) -> bool:
        """휴대폰 번호 유효성 검사"""
        if self.phone is None:
            return True  # OAuth 사용자는 NULL 허용
        return len(self.phone) == 11 and self.phone.isdigit()

    # ==================== 팩토리 메서드 ====================

    @classmethod
    def create_oauth_user(
        cls,
        user_id: str,
        user_name: str,
        email: str,
        google_access_token: str,
        google_refresh_token: str,
        google_token_expires_at: datetime,
        phone: Optional[str] = None
    ) -> "User":
        """OAuth 사용자 생성"""
        return cls(
            user_id=user_id,
            user_name=user_name,
            email=email,
            phone=phone,
            google_access_token=google_access_token,
            google_refresh_token=google_refresh_token,
            google_token_expires_at=google_token_expires_at
        )