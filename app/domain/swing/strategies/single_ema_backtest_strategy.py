"""
단일 20EMA 백테스팅 전략 (Single EMA Backtest Strategy)

실전 매매 전략(single_ema_strategy.py)을 백테스팅용으로 변환.
외국인/프로그램 수급 데이터 대신 OBV(On Balance Volume)를 사용.

Entry Conditions (백테스팅 대체):
1. 가격 위치: 현재가 > EMA20, 괴리율 <= 2%
2. 수급 강도: OBV z-score > 1.0 + 거래량 증가
3. 수급 유지: OBV 전일 대비 상승 (일봉 특성상 1일 체크로 충분)
4. 거래량: 20일 평균 대비 120% 이상
5. 급등 필터: 당일 상승률 <= 7%
6. 연속 확인: CONSECUTIVE_REQUIRED 횟수만큼 조건 충족 필요

Exit Conditions (백테스팅 대체):
1. 고정 손절: -3%
2. 수급 반전: OBV z-score < -1.0
3. EMA 이탈: 2일 연속 EMA 아래
4. 수급 약화: OBV 정체 (z-score 0 근처)
5. 추세 악화: EMA 아래에서 가격 하락 + 이탈폭 증가
"""
import pandas as pd
import talib as ta
import numpy as np
from typing import Dict, List, Optional, Tuple
from .base_strategy import BacktestStrategy


class SingleEMABacktestStrategy(BacktestStrategy):
    """단일 20EMA 백테스팅 전략"""

    # === 기본 파라미터 (실전 매매와 동일) ===
    EMA_PERIOD = 20

    # 진입 조건
    MAX_GAP_RATIO = 0.02  # 괴리율 2% 이내
    VOLUME_RATIO_THRESHOLD = 1.2  # 거래량 120% 이상
    MAX_SURGE_RATIO = 0.07  # 급등 필터 7%
    CONSECUTIVE_REQUIRED = 1  # 조건 충족 시 즉시 진입 (일봉 기준, 다른 조건들이 필터링)

    # 청산 조건
    STOP_LOSS_FIXED = -0.03  # 고정 손절 -3%
    EMA_BREACH_REQUIRED = 2  # EMA 이탈 2일 연속 확인

    # === 백테스팅 전용 파라미터 (OBV 기반) ===
    OBV_Z_BUY_THRESHOLD = 1.0  # 매수 신호 z-score 기준
    OBV_Z_SELL_THRESHOLD = -1.0  # 매도 신호 z-score 기준
    OBV_Z_WEAK_THRESHOLD = 0.3  # 수급 약화 판단 기준
    OBV_LOOKBACK = 7  # OBV z-score 계산 기간
    VOLUME_MA_PERIOD = 20  # 거래량 이동평균 기간

    def __init__(self):
        super().__init__("단일 20EMA 전략")

    def compute(self, prices_df: pd.DataFrame, params: Dict) -> Dict:
        """
        단일 EMA 전략 백테스트 실행

        Args:
            prices_df: 주가 데이터 (STCK_BSOP_DATE, STCK_CLPR, ACML_VOL 필수)
            params: {
                "init_amount": 초기 투자금,
                "buy_ratio": 매수 비율,
                "sell_ratio": 매도 비율,
                "eval_start": 평가 시작일,
                ...
            }

        Returns:
            백테스트 결과
        """
        initial_capital = params["init_amount"]
        buy_ratio = params["buy_ratio"]
        sell_ratio = params["sell_ratio"]
        eval_start = params["eval_start"]

        df = prices_df.copy()
        df = self._prepare_data(df)
        df = self._calculate_indicators(df)

        # 평가 기간 필터링
        eval_df = df[df["STCK_BSOP_DATE"] >= eval_start].copy()

        trades: List[Dict] = []
        current_capital = initial_capital

        # 상태 추적 변수
        entry_consecutive = 0  # 진입 조건 연속 충족 횟수
        ema_breach_count = 0  # EMA 이탈 연속 횟수
        buy_count = 0  # 매수 횟수 (분할 매수용)
        sell_count = 0  # 매도 횟수 (분할 매도용)
        entry_price = None  # 진입가
        prev_gap = None  # 이전 EMA 이탈폭 (추세 악화 체크용)

        for i in range(len(eval_df)):
            idx = eval_df.index[i]
            row = df.loc[idx]
            prev_idx = df.index[df.index.get_loc(idx) - 1] if df.index.get_loc(idx) > 0 else None
            prev_row = df.loc[prev_idx] if prev_idx is not None else None

            if prev_row is None:
                continue

            current_price = row["STCK_CLPR"]
            current_date = row["STCK_BSOP_DATE"]
            ema_20 = row["ema_20"]

            # 현재 포지션 계산
            total_bought = sum(t["quantity"] for t in trades if t["action"] == "BUY")
            total_sold = sum(t["quantity"] for t in trades if t["action"] == "SELL")
            total_quantity = total_bought - total_sold
            has_position = total_quantity > 0

            # === 1단계: 청산 신호 체크 (포지션 있을 때) ===
            if has_position:
                exit_signal, exit_reason = self._check_exit_conditions(
                    row, prev_row, entry_price, ema_breach_count, prev_gap
                )

                # EMA 이탈 카운트 업데이트
                if current_price < ema_20:
                    ema_breach_count += 1
                    current_gap = ema_20 - current_price
                    prev_gap = current_gap
                else:
                    ema_breach_count = 0
                    prev_gap = None

                if exit_signal:
                    curr_qty, curr_avg_cost = self._calculate_position_state(trades)

                    # 분할 매도 로직: 첫 매도는 sell_ratio만큼, 이후는 전량
                    if sell_count == 0:
                        sell_quantity = int(curr_qty * sell_ratio)
                    else:
                        sell_quantity = curr_qty

                    # 매도 수량이 0보다 클 때만 실행
                    if sell_quantity > 0:
                        sell_amount = sell_quantity * current_price
                        realized_pnl = (current_price - curr_avg_cost) * sell_quantity
                        realized_pnl_pct = ((current_price / curr_avg_cost) - 1) * 100 if curr_avg_cost > 0 else 0.0

                        current_capital += sell_amount

                        trades.append({
                            "date": current_date,
                            "action": "SELL",
                            "quantity": sell_quantity,
                            "price": float(current_price),
                            "amount": float(sell_amount),
                            "current_capital": float(current_capital),
                            "realized_pnl": float(realized_pnl),
                            "realized_pnl_pct": round(realized_pnl_pct, 2),
                            "reason": f"{exit_reason} ({sell_count + 1}차 매도)",
                        })

                        # 매도 횟수 증가
                        sell_count += 1

                    # 포지션 완전 청산 시에만 상태 리셋
                    remaining_qty = curr_qty - sell_quantity
                    if remaining_qty <= 0:
                        entry_price = None
                        ema_breach_count = 0
                        prev_gap = None
                        sell_count = 0
                        buy_count = 0  # 매수 횟수도 리셋

            # === 2단계: 진입 신호 체크 (청산 후 포지션 재계산) ===
            # 청산 후 포지션 상태 다시 확인
            total_bought = sum(t["quantity"] for t in trades if t["action"] == "BUY")
            total_sold = sum(t["quantity"] for t in trades if t["action"] == "SELL")
            total_quantity = total_bought - total_sold
            has_position = total_quantity > 0

            # 포지션 없거나 2차 매수 가능한 경우
            can_buy = (not has_position and buy_count == 0) or (has_position and buy_count < 2)

            if can_buy and current_capital > 0:
                entry_signal = self._check_entry_conditions(row)

                if entry_signal:
                    entry_consecutive += 1
                else:
                    entry_consecutive = 0

                # 조건 충족 시 매수
                if entry_consecutive >= self.CONSECUTIVE_REQUIRED:
                    # 분할 매수 로직: 1차는 buy_ratio만큼, 2차는 전량
                    if buy_count == 0:
                        buy_amount = current_capital * buy_ratio  # 1차: 비율만큼
                    else:
                        buy_amount = current_capital  # 2차: 남은 현금 전량

                    buy_quantity = int(buy_amount / current_price)
                    executed_amount = buy_quantity * current_price

                    if buy_quantity > 0:
                        current_capital -= executed_amount

                        # 1차 매수 시에만 진입가 설정
                        if buy_count == 0:
                            entry_price = current_price
                            sell_count = 0  # 매도 횟수 리셋

                        entry_consecutive = 0
                        ema_breach_count = 0
                        prev_gap = None

                        trades.append({
                            "date": current_date,
                            "action": "BUY",
                            "quantity": buy_quantity,
                            "price": float(current_price),
                            "amount": float(executed_amount),
                            "current_capital": float(current_capital),
                            "reason": f"매수 신호 ({buy_count + 1}차, "
                                      f"OBV z={row['obv_z']:.2f}, 괴리율={row['gap_ratio']*100:.2f}%)",
                        })

                        # 매수 횟수 증가
                        buy_count += 1

        # 결과 포맷팅
        result = self._format_result(prices_df, params, trades, current_capital)
        result["parameters"].update({
            "EMA_PERIOD": self.EMA_PERIOD,
            "MAX_GAP_RATIO": self.MAX_GAP_RATIO,
            "STOP_LOSS": self.STOP_LOSS_FIXED,
        })

        return result

    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """데이터 전처리"""
        if "STCK_BSOP_DATE" in df.columns:
            # 날짜 타입 변환
            df["STCK_BSOP_DATE"] = pd.to_datetime(df["STCK_BSOP_DATE"])
            df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)

        for col in ["STCK_CLPR", "ACML_VOL"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술 지표 계산"""
        close = df["STCK_CLPR"].values.astype(float)
        volume = df["ACML_VOL"].values.astype(float)

        # EMA 20
        df["ema_20"] = pd.Series(ta.EMA(close, timeperiod=self.EMA_PERIOD), index=df.index)

        # 괴리율
        df["gap_ratio"] = (df["STCK_CLPR"] - df["ema_20"]) / df["ema_20"]

        # 전일 대비 등락률
        df["daily_return"] = df["STCK_CLPR"].pct_change()

        # OBV
        df["obv"] = pd.Series(ta.OBV(close, volume), index=df.index)

        # OBV z-score
        epsilon = 1e-9
        obv_diff = df["obv"].diff()
        obv_mean = obv_diff.rolling(self.OBV_LOOKBACK, min_periods=3).mean()
        obv_std = obv_diff.rolling(self.OBV_LOOKBACK, min_periods=3).std().replace(0, epsilon).fillna(epsilon)
        df["obv_z"] = ((obv_diff - obv_mean) / obv_std).fillna(0.0)

        # OBV 변화 (상승 여부)
        df["obv_rising"] = obv_diff > 0

        # 거래량 이동평균
        df["volume_ma"] = df["ACML_VOL"].rolling(self.VOLUME_MA_PERIOD, min_periods=5).mean()
        df["volume_ratio"] = df["ACML_VOL"] / df["volume_ma"]

        return df

    def _check_entry_conditions(self, row: pd.Series) -> bool:
        """
        진입 조건 체크 (6개 조건)

        1. 가격 위치: 현재가 > EMA20
        2. 괴리율: <= 2%
        3. 수급 강도: OBV z-score > 1.0
        4. 수급 유지: OBV 전일 대비 상승 (obv_diff > 0으로 이미 계산됨)
        5. 거래량: 20일 평균 대비 120%
        6. 급등 필터: 상승률 <= 7%
        """
        # NaN 체크
        if pd.isna(row["ema_20"]) or pd.isna(row["obv_z"]) or pd.isna(row["volume_ratio"]):
            return False

        current_price = row["STCK_CLPR"]
        ema_20 = row["ema_20"]
        gap_ratio = row["gap_ratio"]
        obv_z = row["obv_z"]
        obv_rising = row["obv_rising"]
        volume_ratio = row["volume_ratio"]
        daily_return = row["daily_return"] if not pd.isna(row["daily_return"]) else 0

        # 조건 1: 가격 위치
        price_above_ema = current_price > ema_20

        # 조건 2: 괴리율
        gap_ok = gap_ratio <= self.MAX_GAP_RATIO

        # 조건 3: 수급 강도 (OBV z-score)
        supply_strong = obv_z > self.OBV_Z_BUY_THRESHOLD

        # 조건 4: 수급 유지 (OBV 상승)
        supply_maintained = obv_rising

        # 조건 5: 거래량
        volume_sufficient = volume_ratio >= self.VOLUME_RATIO_THRESHOLD

        # 조건 6: 급등 필터
        surge_filtered = daily_return <= self.MAX_SURGE_RATIO

        return all([
            price_above_ema,
            gap_ok,
            supply_strong,
            supply_maintained,
            volume_sufficient,
            surge_filtered
        ])

    def _check_exit_conditions(
        self,
        row: pd.Series,
        prev_row: pd.Series,
        entry_price: float,
        ema_breach_count: int,
        prev_gap: Optional[float]
    ) -> Tuple[bool, str]:
        """
        청산 조건 체크 (5개 조건, 우선순위 순)

        1. 고정 손절 -3%
        2. 수급 반전 (OBV z-score < -1.0)
        3. EMA 이탈 (2일 연속)
        4. 수급 약화 (OBV 정체)
        5. 추세 악화 (EMA 아래 + 가격 하락 + 이탈폭 증가)
        """
        current_price = row["STCK_CLPR"]
        ema_20 = row["ema_20"]
        obv_z = row["obv_z"]

        if entry_price is None or pd.isna(entry_price):
            return False, ""

        profit_rate = (current_price - entry_price) / entry_price

        # 1. 고정 손절
        if profit_rate <= self.STOP_LOSS_FIXED:
            return True, f"고정 손절 ({profit_rate*100:.2f}%)"

        # 2. 수급 반전
        if obv_z <= self.OBV_Z_SELL_THRESHOLD:
            return True, f"수급 반전 (OBV z={obv_z:.2f})"

        # 3. EMA 이탈 (2일 연속)
        below_ema = current_price < ema_20
        if below_ema and ema_breach_count + 1 >= self.EMA_BREACH_REQUIRED:
            return True, f"EMA 이탈 ({ema_breach_count + 1}일 연속)"

        # 4. 수급 약화
        if abs(obv_z) < self.OBV_Z_WEAK_THRESHOLD:
            # 추가 조건: 가격이 EMA 아래일 때만 청산
            if below_ema:
                return True, f"수급 약화 + EMA 하회 (OBV z={obv_z:.2f})"

        # 5. 추세 악화
        if below_ema and prev_gap is not None:
            current_gap = ema_20 - current_price
            prev_price = prev_row["STCK_CLPR"] if prev_row is not None else current_price
            price_declined = current_price < prev_price
            gap_increased = current_gap > prev_gap

            if price_declined and gap_increased:
                return True, "추세 악화 (가격 하락 + 이탈폭 증가)"

        return False, ""
