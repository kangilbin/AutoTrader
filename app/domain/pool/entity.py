"""
Pool 도메인 엔티티 - 계좌·시장별 풀(총자산 공유) 매수 설정

풀 설정이 켜진(USE_YN='Y') 계좌·시장의 스윙만 종목별 배정금(CUR_AMOUNT) 대신
총자산 기준 리스크 사이징으로 매수한다. 행이 없거나 꺼져 있으면 기존 방식 그대로다.
매도·청산은 풀과 무관하게 스윙별 청산선으로 처리한다.
"""
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Optional

from sqlalchemy import CHAR, Column, DECIMAL, DateTime, Integer, String
from sqlalchemy.orm import reconstructor

from app.common.database import Base
from app.core.market_code import is_overseas
from app.exceptions import ValidationError

# 동시 보유 종목 수(MAX_POSITIONS)만 사용자가 정한다. 나머지 세 값은 거기서 자동으로 정해지는 파생값이라 직접 바꿀 수 없다.
_DERIVED_FIELDS = ("RISK_PCT", "MAX_POSITION_PCT", "MAX_OPEN_RISK_PCT")

POOL_KR = "KR"
POOL_US = "US"
VALID_POOL_MARKETS = (POOL_KR, POOL_US)

# 매수 규칙 — 집중형 균등 배분 (사용자 결정 2026-11-01): 총자산의 약 95% 를 N종목에 균등하게 나눠 담는다.
#   사용자가 정하는 값은 동시 보유 종목 수 N 하나뿐이고(MIN~MAX_POSITIONS_SETTING), 나머지는 N 에서 자동으로 정해진다:
#     종목당 최대 비중 = 95% ÷ N                          (N=10 → 9.5%, 20 → 4.75%)
#     거래당 리스크    = max(0.70%, 비중 × 가장 넓은 손절폭 7%)  (손절폭이 7% 이하인 종목은 모두 비중에서 잘려 균등해진다)
#     미청산 리스크 한도 = 8% 고정                         (N 이 얼마든 합계가 약 6.7% 라 한도 안)
#   현금 5% 는 수수료·호가 여유와 파킹의 '남길 현금' 이다 — 100% 로 하면 마지막 종목이 현금 부족으로 못 산다.
#   N < 7 은 비중이 13.6% 를 넘어 거래당 리스크가 허용 상한(1%)을 넘으므로 선택할 수 없다.
#   ⚠️ 이 집중형 구성은 백테스트로 검증하지 않았다 (분산형 0.10% · 30종목이 백테스트 기준 구성이었다 — checklist 참고).
#   승률 약 22% 전략이라 연속 손절 구간의 낙폭이 커질 수 있다.
TARGET_EXPOSURE = Decimal("0.95")              # 종목 수만큼 모두 채웠을 때의 총자산 대비 투입 비중
WIDEST_STOP_PCT = Decimal("0.07")              # 가장 넓은 손절폭 (BaseSingleEMAStrategy.MAX_STOP_LOSS_PCT)
MIN_POSITIONS_SETTING = 7
MAX_POSITIONS_SETTING = 30
DEFAULT_RISK_PCT = Decimal("0.0070")           # 거래당 리스크 하한 (N ≥ 10 에서 쓰는 값)
DEFAULT_MAX_POSITIONS = 10                     # 동시 보유 종목 수 기본값
DEFAULT_MAX_POSITION_PCT = Decimal("0.095")    # N = 10 일 때의 종목당 최대 비중 (참고용 — 실제 값은 derive_limits 로 계산)
DEFAULT_MAX_OPEN_RISK_PCT = Decimal("0.08")    # 보유분 총 열린 리스크 / 총자산 (고정)


def derive_limits(max_positions: int) -> tuple:
    """동시 보유 종목 수 → (종목당 최대 비중, 거래당 리스크) — 앱(utils/poolCapacity.ts)이 같은 식으로 미리 보여준다

    비중은 내림(합계가 95% 를 넘지 않게), 리스크는 올림(비중보다 항상 같거나 커서 비중에서 잘리게)한다.
    """
    pct = (TARGET_EXPOSURE / max_positions).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    risk = max(DEFAULT_RISK_PCT, (pct * WIDEST_STOP_PCT).quantize(Decimal("0.00001"), rounding=ROUND_UP))
    return pct, risk


DEFAULT_PARK_RESERVE_PCT = Decimal("0.05")     # 파킹할 때 현금으로 남길 비율 (총자산 대비)
PARK_RESERVE_LIMIT = Decimal("0.50")
# 파킹 ETF는 시장별로 고정한다 (사용자 결정 2026-11-01) — 설정은 남길 현금 비율뿐 (변환은 사용자가 버튼으로 한다)
#   국내: KODEX CD금리액티브(합성) / 미국: iShares 0-3 Month Treasury Bond ETF (SGOV, 달러 단기 국채)
# 풀 시장의 현금과 같은 통화여야 한다 (원화 풀 ↔ 국내 상장, 달러 풀 ↔ 미국 상장)
PARK_ETF = {
    POOL_KR: {"code": "459580", "excg": None, "name": "KODEX CD금리액티브(합성)"},
    POOL_US: {"code": "SGOV", "excg": "NYS", "name": "iShares 0-3 Month Treasury Bond ETF"},
}

# 입력 실수 방지용 상한
RISK_PCT_LIMIT = Decimal("0.01")               # 거래당 리스크 최대 1%
OPEN_RISK_PCT_LIMIT = Decimal("0.20")          # 총 열린 리스크 최대 20%


def is_park_etf(market: str, st_code: str) -> bool:
    """풀 시장의 파킹 ETF 종목인가 — 스윙으로 등록·자동 등록하면 안 된다 (해제 때 같은 보유를 팔게 된다)"""
    return st_code == PARK_ETF[market]["code"]


def pool_market_of(mrkt_code: str) -> str:
    """스윙 시장코드(J/NX/UN/NYS/NAS/AMS) → 풀 시장 구분(KR/US)"""
    return POOL_US if is_overseas(mrkt_code) else POOL_KR


class PoolSetting(Base):
    """풀 설정 엔티티 (계좌·시장별 1행)"""
    __tablename__ = "POOL_SETTING"

    ACCOUNT_NO = Column(String(50), primary_key=True, comment='계좌 번호')
    MARKET = Column(String(2), primary_key=True, comment='풀 시장 구분 (KR: 국내, US: 미국)')
    USE_YN = Column(CHAR(1), nullable=False, default='N', server_default='N', comment='풀 매수 사용 여부')
    MAX_POSITIONS = Column(Integer, nullable=False, default=DEFAULT_MAX_POSITIONS,
                           server_default=str(DEFAULT_MAX_POSITIONS), comment='동시 보유 종목 수 (사용자 설정 — 나머지 규칙이 여기서 정해진다)')
    # RISK_PCT · MAX_POSITION_PCT · MAX_OPEN_RISK_PCT 는 컬럼이 아니다 — MAX_POSITIONS 에서 계산한 읽기용 속성이다
    #   (__init__ / _on_load / update_settings 에서 채운다). 할당기·응답 스키마는 예전처럼 setting.RISK_PCT 로 읽는다.
    # PARK_YN 은 더 이상 쓰지 않는다 (2026-11-03) — 파킹은 풀이 켜져 있으면 항상 허용하고, 변환은 사용자가 버튼으로 한다.
    #   기존 DB 의 NOT NULL 컬럼을 ALTER 없이 두려고 컬럼과 기본값만 남겼다 (읽는 코드 없음 — parking_enabled 참고).
    PARK_YN = Column(CHAR(1), nullable=False, default='N', server_default='N',
                     comment='미사용 (호환용) — 파킹은 풀 사용 여부만 따른다')
    PARK_RESERVE_PCT = Column(DECIMAL(5, 4), nullable=False, default=DEFAULT_PARK_RESERVE_PCT,
                              server_default='0.0500', comment='파킹 시 현금으로 남길 비율 (총자산 대비)')
    REG_DT = Column(DateTime, default=datetime.now, nullable=False, comment='등록일')
    MOD_DT = Column(DateTime, comment='수정일')

    # ==================== 파생값 ====================

    def __init__(self, **kwargs):
        # 파생값은 컬럼이 아니라 생성자에 넘길 수 없다 — 테스트·연구용 재정의만 받는다 (운영 코드는 넘기지 않는다)
        overrides = {k: kwargs.pop(k) for k in _DERIVED_FIELDS if k in kwargs}
        kwargs.setdefault("MAX_POSITIONS", DEFAULT_MAX_POSITIONS)
        super().__init__(**kwargs)
        self._apply_derived(overrides)

    @reconstructor
    def _on_load(self):
        """DB 에서 읽은 행에도 파생값을 채운다 — 저장된 종목 수와 어긋날 수 없다

        허용 범위(7~30) 밖 값이 저장돼 있으면(예전 버전의 행·수동 수정) 범위 안으로 맞춘다 — 그대로 두면 파생된
        거래당 리스크가 상한(1%)을 넘고(N=1 이면 6.65%), 풀 켜기(검증)도 막힌다. 사용자 입력 경로가 아니라 읽을 때만 적용한다
        (테스트·연구가 create(max_positions=...) 로 만드는 값은 건드리지 않는다).
        """
        n = self.MAX_POSITIONS
        if n is not None and not (MIN_POSITIONS_SETTING <= n <= MAX_POSITIONS_SETTING):
            self.MAX_POSITIONS = min(MAX_POSITIONS_SETTING, max(MIN_POSITIONS_SETTING, n))
        self._apply_derived({})

    def _apply_derived(self, overrides: dict) -> None:
        # MAX_POSITIONS 가 None 인 것은 테스트의 '제한 없음' 재정의뿐이다 — 규칙은 기본 종목 수 기준으로 둔다
        n = self.MAX_POSITIONS if self.MAX_POSITIONS else DEFAULT_MAX_POSITIONS
        pct, risk = derive_limits(n)
        self.MAX_POSITION_PCT = Decimal(str(overrides.get("MAX_POSITION_PCT", pct)))
        self.RISK_PCT = Decimal(str(overrides.get("RISK_PCT", risk)))
        self.MAX_OPEN_RISK_PCT = Decimal(str(overrides.get("MAX_OPEN_RISK_PCT", DEFAULT_MAX_OPEN_RISK_PCT)))

    # ==================== 검증 ====================

    def validate(self) -> None:
        """풀 설정 유효성 검증"""
        if not self.ACCOUNT_NO:
            raise ValidationError("계좌번호는 필수입니다")
        if self.MARKET not in VALID_POOL_MARKETS:
            raise ValidationError(f"풀 시장은 {VALID_POOL_MARKETS} 중 하나여야 합니다")
        if self.USE_YN not in ('Y', 'N'):
            raise ValidationError("사용 여부는 [Y,N]이어야 합니다")
        if not (0 < Decimal(self.RISK_PCT) <= RISK_PCT_LIMIT):
            raise ValidationError(f"거래당 리스크는 0 초과 {RISK_PCT_LIMIT} 이하여야 합니다")
        if self.MAX_POSITIONS is not None and self.MAX_POSITIONS < 1:
            raise ValidationError("동시 보유 종목 수 상한은 1 이상이어야 합니다")
        if not (0 < Decimal(self.MAX_POSITION_PCT) <= 1):
            raise ValidationError("종목당 최대 투입 비율은 0 초과 1 이하여야 합니다")
        if not (0 < Decimal(self.MAX_OPEN_RISK_PCT) <= OPEN_RISK_PCT_LIMIT):
            raise ValidationError(f"총 열린 리스크 상한은 0 초과 {OPEN_RISK_PCT_LIMIT} 이하여야 합니다")
        if Decimal(self.RISK_PCT) > Decimal(self.MAX_OPEN_RISK_PCT):
            raise ValidationError("거래당 리스크가 총 열린 리스크 상한보다 클 수 없습니다")
        reserve = Decimal(self.PARK_RESERVE_PCT if self.PARK_RESERVE_PCT is not None else DEFAULT_PARK_RESERVE_PCT)
        if not (0 <= reserve <= PARK_RESERVE_LIMIT):
            raise ValidationError(f"파킹 시 남길 현금 비율은 0 이상 {PARK_RESERVE_LIMIT} 이하여야 합니다")

    # ==================== 상태 조회 ====================

    def is_active(self) -> bool:
        return self.USE_YN == 'Y'

    def parking_enabled(self) -> bool:
        """파킹 기능 사용 — 풀이 켜져 있으면 항상 허용 (변환은 사용자가 버튼으로, 해제는 풀 매수 때 자동)"""
        return self.is_active()

    @property
    def park_code(self) -> str:
        """이 풀 시장의 파킹 ETF 종목코드 (고정)"""
        return PARK_ETF[self.MARKET]["code"]

    @property
    def park_mrkt_code(self) -> str:
        """파킹 ETF 주문에 쓰는 시장코드 (국내 J, 미국 거래소 코드)"""
        return PARK_ETF[self.MARKET]["excg"] or "J"

    # ==================== 생성·변경 ====================

    @classmethod
    def create(
        cls,
        account_no: str,
        market: str,
        use_yn: str = 'N',
        risk_pct: Optional[Decimal] = None,
        max_positions: Optional[int] = DEFAULT_MAX_POSITIONS,
        max_position_pct: Optional[Decimal] = None,
        max_open_risk_pct: Optional[Decimal] = None,
    ) -> "PoolSetting":
        """팩토리 메서드 - 검증 후 반환 (기본은 꺼진 상태)

        운영 코드는 max_positions 만 넘긴다. risk_pct·max_position_pct·max_open_risk_pct 는 종목 수에서 정해지는
        파생값이라, 다른 숫자가 필요한 테스트·연구용 재정의다 (저장되지 않으므로 DB 에서 읽은 행에는 항상 파생값이 들어간다).
        """
        derived = {k: v for k, v in (("RISK_PCT", risk_pct), ("MAX_POSITION_PCT", max_position_pct),
                                      ("MAX_OPEN_RISK_PCT", max_open_risk_pct)) if v is not None}
        setting = cls(
            ACCOUNT_NO=account_no, MARKET=market, USE_YN=use_yn, MAX_POSITIONS=max_positions,
            PARK_RESERVE_PCT=DEFAULT_PARK_RESERVE_PCT, **derived,
        )
        setting.validate()
        return setting

    def update_settings(self, **changes) -> None:
        """부분 수정 후 검증 — 검증에 실패하면 원래 값으로 되돌린다"""
        derived = sorted(set(changes) & set(_DERIVED_FIELDS))
        if derived:
            raise ValidationError(f"동시 보유 종목 수에서 자동으로 정해지는 값이라 직접 바꿀 수 없습니다: {derived}")
        allowed = {"USE_YN", "MAX_POSITIONS", "PARK_RESERVE_PCT"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"수정할 수 없는 항목입니다: {sorted(unknown)}")
        # 비울 수 있는 항목이 없다 — null 은 검증 오류로 거절한다
        required_null = sorted(k for k, v in changes.items() if v is None)
        if required_null:
            raise ValidationError(f"비울 수 없는 항목입니다: {required_null}")
        if "MAX_POSITIONS" in changes:
            n = changes["MAX_POSITIONS"]
            if isinstance(n, bool) or not isinstance(n, int) or not (MIN_POSITIONS_SETTING <= n <= MAX_POSITIONS_SETTING):
                raise ValidationError(
                    f"동시 보유 종목 수는 {MIN_POSITIONS_SETTING}~{MAX_POSITIONS_SETTING} 사이의 정수여야 합니다")
        backup = {k: getattr(self, k) for k in (*changes, *_DERIVED_FIELDS)}
        for key, value in changes.items():
            setattr(self, key, Decimal(str(value)) if key.endswith("_PCT") else value)
        if "MAX_POSITIONS" in changes:
            self._apply_derived({})        # 종목 수가 바뀌면 종목당 최대 비중·거래당 리스크도 따라 바뀐다
        try:
            self.validate()
        except ValidationError:
            for key, value in backup.items():
                setattr(self, key, value)
            raise
        self.MOD_DT = datetime.now()
