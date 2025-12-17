"""
Account 도메인 엔티티 - 비즈니스 로직 캡슐화
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.exceptions.http import BusinessException


@dataclass
class Account:
    """계좌 도메인 엔티티"""
    account_id: Optional[int] = None
    user_id: str = ""
    account_no: str = ""
    auth_id: int = 0
    reg_dt: Optional[datetime] = field(default_factory=datetime.now)
    mod_dt: Optional[datetime] = None

    # ==================== 비즈니스 로직 ====================

    def validate(self) -> None:
        """계좌 유효성 검증"""
        if not self.account_no:
            raise BusinessException("계좌번호는 필수입니다")
        if len(self.account_no) != 10:
            raise BusinessException("계좌번호는 10자리여야 합니다")
        if not self.auth_id:
            raise BusinessException("인증키 ID는 필수입니다")

    def get_cano(self) -> str:
        """계좌번호 앞 8자리 (CANO)"""
        return self.account_no[:8]

    def get_acnt_prdt_cd(self) -> str:
        """계좌번호 뒤 2자리 (ACNT_PRDT_CD)"""
        return self.account_no[8:]

    def update_auth(self, auth_id: int) -> None:
        """인증키 변경"""
        if not auth_id:
            raise BusinessException("인증키 ID는 필수입니다")
        self.auth_id = auth_id
        self.mod_dt = datetime.now()

    # ==================== 팩토리 메서드 ====================

    @classmethod
    def create(cls, user_id: str, account_no: str, auth_id: int) -> "Account":
        """새 계좌 생성"""
        account = cls(
            user_id=user_id,
            account_no=account_no,
            auth_id=auth_id
        )
        account.validate()
        return account