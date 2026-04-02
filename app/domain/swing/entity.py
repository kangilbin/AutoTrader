"""
Swing 도메인 엔티티 - ORM 모델 + 비즈니스 로직
"""
from sqlalchemy import Column, Integer, String, CHAR, DECIMAL, DateTime, Sequence, UniqueConstraint
from datetime import datetime
from decimal import Decimal

from app.common.database import Base
from app.exceptions import ValidationError

VALID_MRKT_CODES = ('J', 'NX', 'UN', 'NASD')


class SwingTrade(Base):
    """스윙 매매 엔티티"""
    __tablename__ = "SWING_TRADE"
    __table_args__ = (
        UniqueConstraint('ACCOUNT_NO', 'MRKT_CODE', 'ST_CODE', name='uq_swing_account_stock'),
    )

    SWING_ID = Column(Integer, Sequence('swing_id_seq'), primary_key=True, comment='스윙 ID')
    ACCOUNT_NO = Column(String(50), nullable=False, comment='계좌 번호')
    MRKT_CODE = Column(String(50), nullable=False, comment='조건 시장 분류 코드(J:KRX, NX:NXT, UN:통합)')
    ST_CODE = Column(String(50), nullable=False, comment='종목 코드')
    USE_YN = Column(CHAR(1), nullable=False, default='N', comment='사용 여부')
    INIT_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='초기 투자금')
    CUR_AMOUNT = Column(DECIMAL(15, 2), nullable=False, comment='현재 투자금')
    SWING_TYPE = Column(CHAR(1), nullable=False, comment='스윙 타입 (A: 이평선, B: 일목균형표)')
    BUY_RATIO = Column(Integer, nullable=False, comment='매수 비율')
    SELL_RATIO = Column(Integer, nullable=False, comment='매도 비율')
    SIGNAL = Column(Integer, nullable=False, default=0, comment='매매 신호 상태 (0:대기, 1:1차매수, 2:2차매수, 3:1차매도)')
    ENTRY_PRICE = Column(DECIMAL(15, 2), nullable=True, comment='평균 매수 단가')
    HOLD_QTY = Column(Integer, nullable=True, default=0, comment='보유 수량')
    EOD_SIGNALS = Column(String(500), nullable=True, comment='EOD 매도 신호 JSON')
    PEAK_PRICE = Column(DECIMAL(15, 2), nullable=True, comment='매수 이후 최고 종가')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')

    # ==================== 검증 ====================

    def validate(self) -> None:
        """스윙 설정 유효성 검증"""
        if not self.ACCOUNT_NO:
            raise ValidationError("계좌번호는 필수입니다")
        if not self.MRKT_CODE:
            raise ValidationError("시장코드는 필수입니다")
        if self.MRKT_CODE not in VALID_MRKT_CODES:
            raise ValidationError(f"시장코드는 {VALID_MRKT_CODES} 중 하나여야 합니다")
        if not self.ST_CODE:
            raise ValidationError("종목코드는 필수입니다")
        if self.SWING_TYPE not in ('S', 'A', 'B'):
            raise ValidationError("스윙 타입은 [S,A,B]여야 합니다")
        if not (0 <= self.BUY_RATIO <= 100):
            raise ValidationError("매수 비율은 0~100 사이여야 합니다")
        if not (0 <= self.SELL_RATIO <= 100):
            raise ValidationError("매도 비율은 0~100 사이여야 합니다")

    # ==================== 상태 조회 ====================

    def is_waiting(self) -> bool:
        """대기 상태 여부 (SIGNAL 0)"""
        return self.SIGNAL == 0

    def is_first_buy_done(self) -> bool:
        """1차 매수 완료 여부 (SIGNAL 1)"""
        return self.SIGNAL == 1

    def is_second_buy_done(self) -> bool:
        """2차 매수 완료 여부 (SIGNAL 2)"""
        return self.SIGNAL == 2

    def is_primary_sold(self) -> bool:
        """1차 매도 완료 여부 (SIGNAL 3)"""
        return self.SIGNAL == 3

    def has_position(self) -> bool:
        """포지션 보유 여부 (SIGNAL 1, 2, 3)"""
        return self.SIGNAL in (1, 2, 3)

    # ==================== 상태 전환 ====================

    def transition_to_first_buy(self, entry_price: int, hold_qty: int, peak_price: int) -> None:
        """1차 매수 완료 (SIGNAL 0 -> 1)"""
        if self.SIGNAL != 0:
            raise ValidationError(f"1차 매수는 대기 상태(0)에서만 가능합니다. 현재: {self.SIGNAL}")
        self.SIGNAL = 1
        self.ENTRY_PRICE = Decimal(entry_price)
        self.HOLD_QTY = hold_qty
        self.PEAK_PRICE = Decimal(peak_price)
        self.MOD_DT = datetime.now()

    def transition_to_second_buy(self, new_entry_price: int, total_hold_qty: int) -> None:
        """2차 매수 완료 (SIGNAL 1 -> 2)"""
        if self.SIGNAL != 1:
            raise ValidationError(f"2차 매수는 1차 매수 상태(1)에서만 가능합니다. 현재: {self.SIGNAL}")
        self.SIGNAL = 2
        self.ENTRY_PRICE = Decimal(new_entry_price)
        self.HOLD_QTY = total_hold_qty
        self.MOD_DT = datetime.now()

    def transition_to_primary_sell(self, remaining_qty: int) -> None:
        """1차 분할 매도 완료 (SIGNAL 1,2 -> 3)"""
        if self.SIGNAL not in (1, 2):
            raise ValidationError(f"1차 매도는 매수 상태(1,2)에서만 가능합니다. 현재: {self.SIGNAL}")
        self.SIGNAL = 3
        self.HOLD_QTY = remaining_qty
        self.MOD_DT = datetime.now()

    def transition_to_reentry(self, new_entry_price: int, total_hold_qty: int, peak_price: int) -> None:
        """재진입 매수 완료 (SIGNAL 3 -> 1)"""
        if self.SIGNAL != 3:
            raise ValidationError(f"재진입은 1차 매도 상태(3)에서만 가능합니다. 현재: {self.SIGNAL}")
        self.SIGNAL = 1
        self.ENTRY_PRICE = Decimal(new_entry_price)
        self.HOLD_QTY = total_hold_qty
        self.PEAK_PRICE = Decimal(peak_price)
        self.MOD_DT = datetime.now()

    def reset_cycle(self) -> None:
        """사이클 종료 — 전량 매도 후 초기화 (SIGNAL -> 0)"""
        self.SIGNAL = 0
        self.ENTRY_PRICE = None
        self.HOLD_QTY = 0
        self.PEAK_PRICE = None
        self.MOD_DT = datetime.now()

    def update_peak_price(self, current_high: int) -> None:
        """장중 고가 갱신"""
        if self.has_position() and current_high > (int(self.PEAK_PRICE) if self.PEAK_PRICE else 0):
            self.PEAK_PRICE = Decimal(current_high)

    def update_hold_qty_partial(self, sold_qty: int) -> None:
        """부분 체결 시 보유 수량 차감"""
        self.HOLD_QTY = (self.HOLD_QTY or 0) - sold_qty
        self.MOD_DT = datetime.now()

    # ==================== 팩토리 메서드 ====================

    @classmethod
    def create(cls, account_no: str, mrkt_code: str, st_code: str,
               init_amount: Decimal, swing_type: str,
               buy_ratio: int = 70, sell_ratio: int = 50) -> "SwingTrade":
        """새 스윙 매매 생성"""
        swing = cls(
            ACCOUNT_NO=account_no,
            MRKT_CODE=mrkt_code,
            ST_CODE=st_code,
            INIT_AMOUNT=init_amount,
            CUR_AMOUNT=init_amount,
            SWING_TYPE=swing_type,
            BUY_RATIO=buy_ratio,
            SELL_RATIO=sell_ratio
        )
        swing.validate()
        return swing


class EmaOption(Base):
    """이평선 옵션 엔티티"""
    __tablename__ = "EMA_OPT"

    ACCOUNT_NO = Column(String(50), nullable=False, primary_key=True, comment='계좌 번호')
    ST_CODE = Column(String(50), nullable=False, primary_key=True, comment='종목 코드')
    SHORT_TERM = Column(Integer, nullable=False, comment='단기 이평선')
    MEDIUM_TERM = Column(Integer, nullable=False, comment='중기 이평선')
    LONG_TERM = Column(Integer, nullable=False, comment='장기 이평선')

    def validate(self) -> None:
        """이평선 옵션 유효성 검증"""
        if not (1 <= self.SHORT_TERM < self.MEDIUM_TERM < self.LONG_TERM):
            raise ValidationError("이평선 기간은 단기 < 중기 < 장기 순이어야 합니다")
