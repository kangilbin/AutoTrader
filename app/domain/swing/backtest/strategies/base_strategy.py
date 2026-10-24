"""
백테스트 전략의 추상 베이스 클래스
"""
import math
from abc import ABC, abstractmethod
from typing import Dict, List
import pandas as pd

from app.core.market_code import is_overseas
from app.core.price import normalize_order_price

# 한국 주식 호가 단위표
_TICK_TABLE = [
    (2_000,    1),
    (5_000,    5),
    (20_000,   10),
    (50_000,   50),
    (200_000,  100),
    (500_000,  500),
    (float("inf"), 1_000),
]


def tick_size(price: float) -> int:
    """가격대별 호가 단위 반환"""
    for threshold, tick in _TICK_TABLE:
        if price < threshold:
            return tick
    return 1_000


def ceil_tick(price: float) -> float:
    """매수용: 호가 단위 올림 (불리한 방향)"""
    t = tick_size(price)
    return math.ceil(price / t) * t


def floor_tick(price: float) -> float:
    """매도용: 호가 단위 내림 (불리한 방향)"""
    t = tick_size(price)
    return math.floor(price / t) * t


# 위 호가표는 국내 전용이다. 미국 종목에 쓰면 $2,000 미만이 1단위가 되어 정수 달러로
# 올림/내림된다($150.21 → 매수 $151). 실전 주문과 같은 규칙을 쓰도록 시장별로 나눈다.
#
# 올림/내림 전에 소수점 6자리로 정리한다 — 실제 가격은 4자리($0.0001)를 넘지 않으므로 그 아래는
# 부동소수 오차뿐이다. 정리하지 않으면 101 + 2×(101−97.9) = 107.19999… 가 107.19로 내림되는 식으로
# 호가 한 단위가 깎이거나 붙는다.
_PRICE_NOISE_DIGITS = 6


def buy_tick(price: float, mrkt_code: str = "") -> float:
    """매수 체결가: 시장별 호가 단위로 올림 (불리한 방향)"""
    price = round(price, _PRICE_NOISE_DIGITS)
    if is_overseas(mrkt_code):
        return normalize_order_price(price, is_buy=True)
    return ceil_tick(price)


def sell_tick(price: float, mrkt_code: str = "") -> float:
    """매도 체결가: 시장별 호가 단위로 내림 (불리한 방향)"""
    price = round(price, _PRICE_NOISE_DIGITS)
    if is_overseas(mrkt_code):
        return normalize_order_price(price, is_buy=False)
    return floor_tick(price)


def tick_unit(price: float, mrkt_code: str = "") -> float:
    """호가 한 단위 — 국내 호가표 / 미국 $0.01 ($1 미만 $0.0001)"""
    if is_overseas(mrkt_code):
        return 0.01 if price >= 1 else 0.0001
    return tick_size(price)


# 슬리피지 — 실제 체결은 신호 가격보다 호가 한두 단위 불리하게 난다 (시장가·호가 주문 모두).
# 판정 기준가(청산선·손절가)에는 붙이지 않고 체결가에만 붙인다.
SLIPPAGE_TICKS = 1


def fill_buy(price: float, mrkt_code: str = "") -> float:
    """매수 체결가 = 호가 올림 + 슬리피지"""
    px = buy_tick(price, mrkt_code)
    return round(px + SLIPPAGE_TICKS * tick_unit(px, mrkt_code), _PRICE_NOISE_DIGITS)


def fill_sell(price: float, mrkt_code: str = "") -> float:
    """매도 체결가 = 호가 내림 − 슬리피지"""
    px = sell_tick(price, mrkt_code)
    return round(px - SLIPPAGE_TICKS * tick_unit(px, mrkt_code), _PRICE_NOISE_DIGITS)


# 거래 비용 (수수료율, 매도 거래세율) — 미국은 거래세가 없다 (매도 SEC 수수료 0.003% 미만은 생략)
_TRADE_COSTS = {"KR": (0.00147, 0.0020), "US": (0.0025, 0.0)}


def trade_costs(mrkt_code: str = "") -> tuple:
    """(수수료율, 매도 거래세율)"""
    return _TRADE_COSTS["US" if is_overseas(mrkt_code) else "KR"]


def records_without_nan(df: pd.DataFrame, columns: List[str]) -> List[Dict]:
    """선택 컬럼을 JSON 레코드로 변환하되 NaN 을 None 으로 바꾼다.

    `Series.where(cond, None)` 은 float 컬럼에서 None 을 다시 NaN 으로 캐스팅하므로
    NaN 가드로 쓸 수 없다 (JSONResponse 는 allow_nan=False 라 그대로 500 이 된다).
    변환은 to_dict 이후 파이썬 스칼라 단계에서 해야 실제로 null 이 나간다.
    """
    records = df[columns].to_dict(orient="records")
    for row in records:
        for key, value in row.items():
            if isinstance(value, float) and math.isnan(value):
                row[key] = None
    return records


def first_eval_date(prices_df: pd.DataFrame, eval_df: pd.DataFrame, eval_start) -> pd.Timestamp:
    """실제로 평가된 첫 봉의 날짜 (요청한 평가 시작일이 아니라)

    보유 데이터가 평가 구간보다 짧으면 평가는 요청 시작일이 아니라 데이터
    시작점부터 이뤄진다. 요청값을 그대로 응답에 쓰면 1년치 결과를 2년
    백테스트로 읽게 되므로, 구간 길이가 결과 해석을 좌우한다.
    """
    source = eval_df if eval_df is not None else prices_df
    dates = source["STCK_BSOP_DATE"]
    if not pd.api.types.is_datetime64_any_dtype(dates):
        dates = pd.to_datetime(dates, format="%Y%m%d")   # DB 원본은 String(8)

    evaluated = dates[dates >= pd.Timestamp(eval_start)]
    return evaluated.min() if not evaluated.empty else pd.Timestamp(eval_start)


class BacktestStrategy(ABC):
    """백테스트 전략 베이스 클래스"""

    def __init__(self, name: str):
        self.name = name

    def min_bars(self, params: Dict) -> int:
        """이 전략이 신호를 낼 수 있는 최소 봉 수

        지표 기간을 아는 전략 자신이 답한다. 서비스에 전략별 표를 두면
        전략의 기간이 바뀔 때 조용히 어긋난다.

        기본값은 가장 긴 EMA 기간 + 1 (교차 판정에 직전 봉이 필요).
        """
        return int(params.get("long_term") or 60) + 1

    @abstractmethod
    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        """
        백테스트 실행 (하위 클래스에서 구현 필수)

        Args:
            prices_df: 주가 데이터 DataFrame
            params: 전략 파라미터 딕셔너리

        Returns:
            백테스트 결과 딕셔너리
        """
        pass

    # ==================== 체결 기록 헬퍼 (전략 공용) ====================

    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        if "STCK_BSOP_DATE" in df.columns:
            df["STCK_BSOP_DATE"] = pd.to_datetime(df["STCK_BSOP_DATE"], format="%Y%m%d")
            df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)
        for col in ["STCK_CLPR", "STCK_HGPR", "STCK_LWPR", "STCK_OPRC", "ACML_VOL"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _execute_buy(self, trades: List[Dict], date, price: float, quantity: int, current_capital: float, reasons: List[str],
                     mrkt_code: str = ""):
        """매수 실행 및 거래 내역 추가"""
        executed_amount = quantity * price
        commission = executed_amount * trade_costs(mrkt_code)[0]
        total_cost = executed_amount + commission

        trades.append({
            'date': date,
            'action': 'BUY',
            'quantity': quantity,
            'price': float(price),
            'amount': float(executed_amount),
            'commission': float(commission),
            'current_capital': float(current_capital - total_cost),
            'reasons': reasons
        })

        return current_capital - total_cost

    def _execute_partial_sell(self, trades: List[Dict], date, price: float, sell_quantity: int,
                             current_capital: float, reasons: List[str], mrkt_code: str = ""):
        """부분 매도 (1차 익절 50%) 실행"""
        curr_qty, curr_avg_cost = self._calculate_position_state(trades)
        if sell_quantity <= 0 or curr_qty <= 0:
            return current_capital

        sell_amount = sell_quantity * price
        commission_rate, tax_rate = trade_costs(mrkt_code)
        commission = sell_amount * commission_rate
        tax = sell_amount * tax_rate
        net_proceeds = sell_amount - commission - tax
        realized_pnl = (price - curr_avg_cost) * sell_quantity - commission - tax
        realized_pnl_pct = ((price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0

        trades.append({
            'date': date, 'action': 'SELL', 'quantity': sell_quantity,
            'price': float(price), 'amount': float(sell_amount),
            'commission': float(commission), 'tax': float(tax),
            'net_proceeds': float(net_proceeds),
            'current_capital': float(current_capital + net_proceeds),
            'realized_pnl': float(realized_pnl),
            'realized_pnl_pct': round(realized_pnl_pct, 2),
            'reasons': reasons
        })
        return current_capital + net_proceeds

    def _execute_sell(self, trades: List[Dict], date, price: float, current_capital: float, reasons: List[str],
                      sell_all: bool = False, mrkt_code: str = ""):
        """전량 매도 실행"""
        curr_qty, curr_avg_cost = self._calculate_position_state(trades)

        if curr_qty == 0:
            return current_capital

        sell_quantity = curr_qty

        if sell_quantity == 0:
            return current_capital

        # 매도 금액 및 수수료/세금 계산
        sell_amount = sell_quantity * price
        commission_rate, tax_rate = trade_costs(mrkt_code)
        commission = sell_amount * commission_rate
        tax = sell_amount * tax_rate
        net_proceeds = sell_amount - commission - tax

        # 실현 손익 계산
        realized_pnl = (price - curr_avg_cost) * sell_quantity - commission - tax
        realized_pnl_pct = ((price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0

        trades.append({
            'date': date,
            'action': 'SELL',
            'quantity': sell_quantity,
            'price': float(price),
            'amount': float(sell_amount),
            'commission': float(commission),
            'tax': float(tax),
            'net_proceeds': float(net_proceeds),
            'current_capital': float(current_capital + net_proceeds),
            'realized_pnl': float(realized_pnl),
            'realized_pnl_pct': round(realized_pnl_pct, 2),
            'reasons': reasons
        })

        return current_capital + net_proceeds

    def _liquidate_final_position(self, trades: List[Dict], eval_df: pd.DataFrame, current_capital: float,
                                  mrkt_code: str = "") -> float:
        """최종 포지션 청산 — 평가 종료 시점 종가로 평가 (실제 주문이 아니라 슬리피지 없음, 비용은 반영)"""
        curr_qty, curr_avg_cost = self._calculate_position_state(trades)

        if curr_qty > 0:
            final_price = eval_df.iloc[-1]["STCK_CLPR"]
            final_date = eval_df.iloc[-1]["STCK_BSOP_DATE"]

            current_capital = self._execute_sell(
                trades,
                final_date,
                final_price,
                current_capital,
                ["최종 청산"],
                sell_all=True,
                mrkt_code=mrkt_code,
            )

        return current_capital

    def _calculate_position_state(self, trades: List[Dict]) -> tuple:
        """
        현재 포지션 상태 계산 (공통 로직)

        Args:
            trades: 거래 내역 리스트

        Returns:
            (보유 수량, 평균 단가)
        """
        position_qty = 0
        position_cost = 0.0

        for trade in trades:
            if trade['action'] == 'BUY':
                position_cost += trade['quantity'] * trade['price']
                position_qty += trade['quantity']
            elif trade['action'] == 'SELL' and position_qty > 0:
                avg_cost = position_cost / position_qty
                sell_qty = trade['quantity']
                position_cost -= avg_cost * sell_qty
                position_qty -= sell_qty

        avg_cost_now = (position_cost / position_qty) if position_qty > 0 else 0.0
        return position_qty, avg_cost_now

    def _format_result(
            self,
            prices_df: pd.DataFrame,
            params: Dict,
            trades: List[Dict],
            final_capital: float,
            eval_df: pd.DataFrame = None
    ) -> Dict:
        """
        백테스트 결과 포맷팅 (공통 로직)

        Args:
            prices_df: 주가 데이터
            params: 파라미터
            trades: 거래 내역
            final_capital: 최종 자본금
            eval_df: 지표 계산된 평가 기간 DataFrame (차트 데이터용)

        Returns:
            포맷팅된 결과 딕셔너리
        """
        initial_capital = params["init_amount"]
        total_return = ((final_capital - initial_capital) / initial_capital) * 100

        result = {
            "strategy_name": self.name,
            "start_date": first_eval_date(prices_df, eval_df, params["eval_start"]).strftime("%Y-%m-%d"),
            "end_date": str(prices_df["STCK_BSOP_DATE"].max()),
            "initial_capital": initial_capital,
            "final_capital": final_capital,
            "total_return": round(total_return, 2),
            "total_trades": len(trades),
            "parameters": {
                "ST_CODE": params["st_code"],
                "SWING_TYPE": params["swing_type"],
            },
            "trades": trades,
        }

        if eval_df is not None and "ema20" in eval_df.columns:
            chart_df = eval_df.copy()
            chart_df["STCK_BSOP_DATE"] = chart_df["STCK_BSOP_DATE"].dt.strftime("%Y%m%d")
            chart_df["ema20"] = chart_df["ema20"].round(2)
            # 평가 구간이 데이터 시작점과 겹치면(보유 2년 미만) EMA20 워밍업 19봉이
            # 여기 포함되어 NaN 으로 남는다. 날짜 축을 price_history 와 맞춰야 하므로
            # 행을 지우지 않고 null 로 내보낸다 (차트에서 선이 끊긴 구간으로 보인다).
            result["price_history"] = records_without_nan(
                chart_df,
                ["STCK_BSOP_DATE", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL"],
            )
            result["ema20_history"] = records_without_nan(chart_df, ["STCK_BSOP_DATE", "ema20"])

        return result
