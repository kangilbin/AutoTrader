"""가격 표현 유틸 - 국내(정수 원화)와 해외(소수점 단가) 공용

국내는 호가 단위가 1원 이상이라 정수로 다뤄도 손실이 없지만, 해외(미국)는
$0.01 단위라 int()로 자르면 $1까지 오차가 난다. ATR이 $2~5인 종목에서
이 오차는 손절·trailing stop 판정을 뒤집으므로, 가격은 float/Decimal로 다루고
DB 저장 시점에만 컬럼 정밀도(DECIMAL(15,2))로 맞춘다.

domain(계산)·external(주문 전송) 양 계층이 공용으로 쓰므로 app/core에 둔다.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

# DB 가격 컬럼 정밀도 (TRADE_PRICE, ENTRY_PRICE, PEAK_PRICE 모두 DECIMAL(15,2))
PRICE_EXP = Decimal("0.01")

# 미국 주식 최소 호가 단위 (SEC Rule 612): $1 이상 $0.01, 미만 $0.0001
TICK_ABOVE_DOLLAR = Decimal("0.01")
TICK_SUB_DOLLAR = Decimal("0.0001")


def to_price(value) -> Decimal:
    """DB 저장용 가격 — 소수점 2자리 Decimal로 반올림

    float를 Decimal()에 바로 넣으면 이진 부동소수 오차가 그대로 들어오므로
    반드시 str()을 거친다.
    """
    return Decimal(str(value)).quantize(PRICE_EXP, rounding=ROUND_HALF_UP)


def round_price(value) -> float:
    """계산/로그용 가격 — 소수점 2자리 float"""
    return float(to_price(value))


def normalize_order_price(price, is_buy: bool) -> float:
    """해외 주문 단가를 최소 호가 단위에 맞춘다

    매수는 올림, 매도는 내림 — 반올림 때문에 목표 호가에 못 미쳐
    체결되지 않는 상황을 막기 위함이다.
    """
    value = Decimal(str(price))
    tick = TICK_ABOVE_DOLLAR if value >= 1 else TICK_SUB_DOLLAR
    rounding = ROUND_CEILING if is_buy else ROUND_FLOOR
    return float(value.quantize(tick, rounding=rounding))
