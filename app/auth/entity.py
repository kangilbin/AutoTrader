"""
Auth 도메인 엔티티 - 비즈니스 로직 캡슐화
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.common.exceptions import BusinessException


@dataclass
class Auth:
    """인증키 도메인 엔티티"""
    auth_id: Optional[int] = None
    user_id: str = ""
    auth_name: str = ""
    simulation_yn: str = "N"
    api_key: str = ""
    secret_key: str = ""
    reg_dt: Optional[datetime] = field(default_factory=datetime.now)
    mod_dt: Optional[datetime] = None

    # ==================== 비즈니스 로직 ====================

    def validate(self) -> None:
        """인증키 유효성 검증"""
        if not self.auth_name:
            raise BusinessException("인증키 이름은 필수입니다")
        if self.simulation_yn not in ('Y', 'N'):
            raise BusinessException("모의투자 여부는 Y 또는 N이어야 합니다")
        if not self.api_key:
            raise BusinessException("API 키는 필수입니다")
        if not self.secret_key:
            raise BusinessException("시크릿 키는 필수입니다")

    def is_simulation(self) -> bool:
        """모의투자 여부"""
        return self.simulation_yn == 'Y'

    def update(self, auth_name: str = None, simulation_yn: str = None) -> None:
        """인증키 정보 수정"""
        if auth_name:
            self.auth_name = auth_name
        if simulation_yn:
            if simulation_yn not in ('Y', 'N'):
                raise BusinessException("모의투자 여부는 Y 또는 N이어야 합니다")
            self.simulation_yn = simulation_yn
        self.mod_dt = datetime.now()

    # ==================== 팩토리 메서드 ====================

    @classmethod
    def create(cls, user_id: str, auth_name: str, simulation_yn: str,
               api_key: str, secret_key: str) -> "Auth":
        """새 인증키 생성"""
        auth = cls(
            user_id=user_id,
            auth_name=auth_name,
            simulation_yn=simulation_yn,
            api_key=api_key,
            secret_key=secret_key
        )
        auth.validate()
        return auth