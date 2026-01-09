"""
Swing 도메인 엔티티 - 비즈니스 로직 캡슐화
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from decimal import Decimal

from app.exceptions import DatabaseError, ValidationError


@dataclass
class SwingTrade:
    """스윙 매매 도메인 엔티티"""
    swing_id: Optional[int] = None
    account_no: str = ""
    st_code: str = ""
    use_yn: str = "Y"
    init_amount: Decimal = Decimal(0)
    cur_amount: Decimal = Decimal(0)
    swing_type: str = "S"  # S: 단일 이평선, B: 일목균형표
    buy_ratio: int = 50
    sell_ratio: int = 50
    signal: int = 0  # 매매 신호 상태 (0:대기, 1:1차매수, 2:2차매수, 3:매도)
    reg_dt: Optional[datetime] = field(default_factory=datetime.now)
    mod_dt: Optional[datetime] = None

    # ==================== 비즈니스 로직 ====================

    def validate(self) -> None:
        """스윙 설정 유효성 검증"""
        if not self.account_no:
            raise ValidationError("계좌번호는 필수입니다")
        if not self.st_code:
            raise ValidationError("종목코드는 필수입니다")
        if self.swing_type not in ('S', 'A', 'B'):
            raise ValidationError("스윙 타입은 [S,A,B]여야 합니다")
        if not (0 <= self.buy_ratio <= 100):
            raise ValidationError("매수 비율은 0~100 사이여야 합니다")
        if not (0 <= self.sell_ratio <= 100):
            raise ValidationError("매도 비율은 0~100 사이여야 합니다")

    def is_active(self) -> bool:
        """활성화 여부"""
        return self.use_yn == 'Y'

    def activate(self) -> None:
        """활성화"""
        self.use_yn = 'Y'
        self.mod_dt = datetime.now()

    def deactivate(self) -> None:
        """비활성화"""
        self.use_yn = 'N'
        self.mod_dt = datetime.now()

    def is_ema_strategy(self) -> bool:
        """이평선 전략 여부"""
        return self.swing_type == 'A'

    def is_single_ema_strategy(self) -> bool:
        """단일 이평선 전략 여부"""
        return self.swing_type == 'S'

    def is_ichimoku_strategy(self) -> bool:
        """일목균형표 전략 여부"""
        return self.swing_type == 'B'

    def get_profit_rate(self) -> Decimal:
        """수익률 계산 (%)"""
        if self.init_amount == 0:
            return Decimal(0)
        return ((self.cur_amount - self.init_amount) / self.init_amount) * 100

    def update_current_amount(self, amount: Decimal) -> None:
        """현재 투자금 업데이트"""
        self.cur_amount = amount
        self.mod_dt = datetime.now()

    def calculate_buy_amount(self) -> Decimal:
        """매수 금액 계산"""
        return self.init_amount * Decimal(self.buy_ratio) / Decimal(100)

    def calculate_sell_amount(self) -> Decimal:
        """매도 금액 계산"""
        return self.cur_amount * Decimal(self.sell_ratio) / Decimal(100)

    # ==================== 신호 상태 관리 ====================

    def is_waiting(self) -> bool:
        """대기 상태 여부"""
        return self.signal == 0

    def is_first_buy_done(self) -> bool:
        """1차 매수 완료 여부"""
        return self.signal == 1

    def is_second_buy_done(self) -> bool:
        """2차 매수 완료 여부"""
        return self.signal == 2

    def is_sold(self) -> bool:
        """매도 완료 여부"""
        return self.signal == 3

    def transition_to_first_buy(self) -> None:
        """1차 매수 상태로 전환"""
        if self.signal != 0:
            raise ValidationError(f"1차 매수는 대기 상태(0)에서만 가능합니다. 현재 상태: {self.signal}")
        self.signal = 1
        self.mod_dt = datetime.now()

    def transition_to_second_buy(self) -> None:
        """2차 매수 상태로 전환"""
        if self.signal != 1:
            raise ValidationError(f"2차 매수는 1차 매수 상태(1)에서만 가능합니다. 현재 상태: {self.signal}")
        self.signal = 2
        self.mod_dt = datetime.now()

    def transition_to_sold(self) -> None:
        """매도 완료 상태로 전환"""
        if self.signal not in (1, 2):
            raise ValidationError(f"매도는 매수 완료 상태(1,2)에서만 가능합니다. 현재 상태: {self.signal}")
        self.signal = 3
        self.mod_dt = datetime.now()

    def reset_signal(self) -> None:
        """신호 초기화 (매도 후 새로운 사이클 시작)"""
        self.signal = 0
        self.mod_dt = datetime.now()

    # ==================== 팩토리 메서드 ====================

    @classmethod
    def create(cls, account_no: str, st_code: str, init_amount: Decimal,
               swing_type: str, buy_ratio: int = 50, sell_ratio: int = 50) -> "SwingTrade":
        """새 스윙 매매 생성"""
        swing = cls(
            account_no=account_no,
            st_code=st_code,
            init_amount=init_amount,
            cur_amount=init_amount,
            swing_type=swing_type,
            buy_ratio=buy_ratio,
            sell_ratio=sell_ratio
        )
        swing.validate()
        return swing


@dataclass
class EmaOption:
    """이평선 옵션 엔티티"""
    account_no: str
    st_code: str
    short_term: int = 5
    medium_term: int = 20
    long_term: int = 60

    def validate(self) -> None:
        """이평선 옵션 유효성 검증"""
        if not (1 <= self.short_term < self.medium_term < self.long_term):
            raise ValidationError("이평선 기간은 단기 < 중기 < 장기 순이어야 합니다")