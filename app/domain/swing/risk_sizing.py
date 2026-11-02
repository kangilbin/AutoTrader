"""
리스크 기반 포지션 사이징 (순수 함수)

총자산 하나를 풀로 공유하고, 종목마다 "청산선에서 나가면 총자산의 risk_pct 만 잃는" 수량을 산정한다.
백테스트 엔진과 실전 매수 경로가 **같은 함수**를 호출해 판정이 어긋나지 않게 한다 — 외부 의존성을 두지 않는다.

한도는 **신규 매수만** 막는다. 보유 중인 포지션을 청산하는 용도로 쓰지 않는다
(강제 청산은 급락 때 한꺼번에 던져 슬리피지만 키운다).
"""
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

# 한도 검사 거절 사유 — 사유별 건너뛴 신호 수를 집계하는 키로도 쓴다
REJECT_CASH = "cash"                      # 현금 부족
REJECT_MAX_POSITIONS = "max_positions"    # 동시 보유 종목 수 상한
REJECT_OPEN_RISK = "open_risk"            # 총 열린 리스크 상한
REJECT_LIQUIDITY = "liquidity"            # 일평균 거래대금 대비 주문 금액 초과

# 수량을 정한 제약
BINDING_RISK = "risk"                     # 1% 리스크 수량이 그대로 적용됨
BINDING_POSITION_CAP = "position_cap"     # 종목당 최대 금액 상한이 먼저 걸림


@dataclass(frozen=True)
class SizingResult:
    qty: int
    risk_amount: float    # 이 수량이 청산선에서 잃는 금액 (qty × 주당 손절폭)
    binding: str          # BINDING_RISK | BINDING_POSITION_CAP (qty == 0 이면 BINDING_RISK)


def calc_qty(
    equity: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    max_position_pct: Optional[float] = None,
) -> SizingResult:
    """리스크 기준 수량 = floor(총자산 × risk_pct ÷ 주당 손절폭), 종목당 금액 상한으로 자른다

    Args:
        equity: 총자산 (현금 + 보유 평가금)
        risk_pct: 손절 시 잃을 총자산 비율 (0.01 = 1%)
        entry_price: 예상 체결가
        stop_price: 진입 시점 청산선 (진입가보다 낮아야 한다)
        max_position_pct: 종목당 최대 투입 금액 / 총자산 (None 이면 상한 없음)

    Returns:
        SizingResult — 입력이 비정상이거나 손절폭이 0 이하면 수량 0
    """
    per_share_risk = entry_price - stop_price
    if equity <= 0 or risk_pct <= 0 or entry_price <= 0 or per_share_risk <= 0:
        return SizingResult(0, 0.0, BINDING_RISK)

    qty = int(equity * risk_pct // per_share_risk)
    binding = BINDING_RISK

    if max_position_pct is not None:
        cap_qty = int(equity * max_position_pct // entry_price)
        if cap_qty < qty:
            qty, binding = cap_qty, BINDING_POSITION_CAP

    if qty <= 0:
        return SizingResult(0, 0.0, binding)
    return SizingResult(qty, qty * per_share_risk, binding)


def position_open_risk(entry_price: float, exit_line: float, qty: int) -> float:
    """포지션 하나의 열린 리스크 = max(0, 진입가 − 현재 청산선) × 수량

    청산선이 본전 이상으로 올라가면 0 — 이익 구간 포지션은 한도를 차지하지 않는다.
    """
    if qty <= 0:
        return 0.0
    return max(0.0, entry_price - exit_line) * qty


def total_open_risk(positions: Iterable[Tuple[float, float, int]]) -> float:
    """보유 포지션 전체의 열린 리스크 합 — positions: (진입가, 현재 청산선, 수량)"""
    return sum(position_open_risk(entry, line, qty) for entry, line, qty in positions)


def check_limits(
    order_amount: float,
    new_risk: float,
    cash: float,
    equity: float,
    position_count: int,
    open_risk: float,
    max_positions: Optional[int] = None,
    max_open_risk_pct: Optional[float] = None,
) -> Optional[str]:
    """신규 매수 가능 여부 — 통과하면 None, 막히면 거절 사유(REJECT_*)

    Args:
        order_amount: 이번 주문 금액 (수량 × 체결가, 비용 제외)
        new_risk: 이번 주문의 리스크 금액 (SizingResult.risk_amount)
        cash: 가용 현금
        equity: 총자산
        position_count: 현재 보유 종목 수
        open_risk: 현재 보유분의 총 열린 리스크
        max_positions: 동시 보유 종목 수 상한 (None 이면 제한 없음)
        max_open_risk_pct: 총 열린 리스크 / 총자산 상한 (None 이면 제한 없음)
    """
    if order_amount > cash:
        return REJECT_CASH
    if max_positions is not None and position_count >= max_positions:
        return REJECT_MAX_POSITIONS
    if max_open_risk_pct is not None and open_risk + new_risk > equity * max_open_risk_pct:
        return REJECT_OPEN_RISK
    return None


def within_liquidity(order_amount: float, avg_daily_amount: float, adv_ratio: float) -> bool:
    """주문 금액이 일평균 거래대금의 adv_ratio 이하인가 (주문은 분할 없이 한 번에 나간다)

    일평균 거래대금을 모르면(0 이하) 확인 불가로 보고 막는다 (fail-closed).
    """
    if avg_daily_amount <= 0:
        return False
    return order_amount <= avg_daily_amount * adv_ratio
