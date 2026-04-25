"""
단일 20EMA 백테스팅 전략 (Single EMA Backtest Strategy)

실전 매매 전략(single_ema_strategy.py)을 백테스팅용으로 변환.
외국인/프로그램 수급 데이터 대신 OBV(On Balance Volume)를 사용.

Entry Conditions (1차 매수):
1. EMA 추세: 현재가 > EMA20 (추세 확인)
2. 수급 강도: OBV z-score > 1.0 (거래량 강도 포함)
3. 급등 필터: 당일 상승률 <= 5%
4. 괴리율 필터: EMA 괴리율 <= 5% (고점 매수 방지)
5. 추세 강도: ADX > 25 (횡보장 차단)
6. 추세 방향: +DI > -DI (상승 추세 확인)

Entry Conditions (2차 매수):
1. 가격 범위: 1차 매수가 대비 +1% ~ +6%
2. EMA 위치: 현재가 > EMA20 (추세 확인)
3. 수급 강도: OBV z-score >= 1.1 (거래량 강도 포함)
4. 손절 안전거리: 현재가 >= 손절가 × 1.04 (4% 안전 마진)

Exit Conditions:
1. 고정 손절: -3%
2. 수급 반전: OBV z-score < -1.0
3. EMA-ATR 손절: 현재가 <= EMA - (ATR × 1.0)
4. 수급 약화: OBV 정체 (z-score 0 근처) + EMA 하회
5. 추세 악화: EMA 아래에서 가격 하락 + 이탈폭 증가
"""
import logging
from datetime import datetime
import pandas as pd
import talib as ta
from typing import Dict, List, Optional, Tuple
from .base_strategy import BacktestStrategy


class SingleEMABacktestStrategy(BacktestStrategy):
    """단일 20EMA 백테스팅 전략"""

    # === 기본 파라미터 ===
    EMA_PERIOD = 20

    # 1차 매수 진입 조건
    MAX_SURGE_RATIO = 0.05  # 급등 필터 5%
    CONSECUTIVE_REQUIRED = 1  # 조건 충족 시 즉시 진입 (일봉 기준)

    # 청산 조건
    STOP_LOSS_FIXED = -0.03  # 고정 손절 -3%
    EMA_BREACH_REQUIRED = 2  # EMA 이탈 2일 연속 확인

    # === 백테스팅 전용 파라미터 (OBV 기반) ===
    OBV_Z_BUY_THRESHOLD = 1.0  # 매수 신호 z-score 기준
    OBV_Z_SELL_THRESHOLD = -1.0  # 매도 신호 z-score 기준
    OBV_Z_WEAK_THRESHOLD = 0.3  # 수급 약화 판단 기준
    OBV_LOOKBACK = 7  # OBV z-score 계산 기간

    # === 진입 조건 필터 ===
    MAX_GAP_RATIO = 0.05  # 괴리율 5% 이하만 진입 (과열 필터)
    ADX_THRESHOLD = 25  # ADX 25 이상 (횡보장 차단)

    # === 손절 조건 ===
    ATR_MULTIPLIER = 1.0  # EMA-ATR 손절 배수

    # === 2차 매수 조건 ===
    SECOND_BUY_PRICE_GAIN_MIN = 0.01  # 최소 1% 상승
    SECOND_BUY_PRICE_GAIN_MAX = 0.06  # 최대 6% 상승 (완화)
    SECOND_BUY_OBV_THRESHOLD = 1.1  # OBV z-score 1.1 이상 (완화)
    SECOND_BUY_SAFETY_MARGIN = 0.04  # 손절가 위 4% 안전 마진

    # === 2차 매도 조건 ===
    SECOND_SELL_DROP_THRESHOLD = -0.02  # 1차 매도가 대비 -2% 추가 하락
    SECOND_SELL_OBV_THRESHOLD = -1.5  # 수급 급락 기준

    # === 거래 비용 ===
    COMMISSION_RATE = 0.00147  # 한국 투자증권 코스피/코스닥 매매 수수료 0.147%
    TAX_RATE = 0.0020  # 매도 시 세금 0.20% (코스피/코스닥)

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
        eval_start = pd.to_datetime(params["eval_start"])

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
        first_sell_price = None  # 1차 매도가 (2차 매도 조건용)

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

            if current_date > datetime(2025, 9, 8):
                logging.info("확인해볼까용")

            # 현재 포지션 계산
            total_bought = sum(t["quantity"] for t in trades if t["action"] == "BUY")
            total_sold = sum(t["quantity"] for t in trades if t["action"] == "SELL")
            total_quantity = total_bought - total_sold
            has_position = total_quantity > 0

            # === 1단계: 청산 신호 체크 (포지션 있을 때) ===
            if has_position:
                exit_signal, exit_reason = self._check_exit_conditions(
                    row, prev_row, entry_price, ema_breach_count, prev_gap,
                    sell_count, first_sell_price
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
                        gross_sell_amount = sell_quantity * current_price
                        sell_commission = gross_sell_amount * self.COMMISSION_RATE  # 매도 수수료 적용
                        sell_tax = gross_sell_amount * self.TAX_RATE  # 매도 세금 적용
                        total_sell_fees = sell_commission + sell_tax
                        net_sell_amount = gross_sell_amount - total_sell_fees

                        realized_pnl_before_fees = (current_price - curr_avg_cost) * sell_quantity
                        realized_pnl = realized_pnl_before_fees - total_sell_fees
                        realized_pnl_pct = (realized_pnl / (curr_avg_cost * sell_quantity)) * 100 if curr_avg_cost * sell_quantity > 0 else 0.0

                        current_capital += net_sell_amount  # 순 매도 금액 합산

                        trades.append({
                            "date": current_date,
                            "action": "SELL",
                            "quantity": sell_quantity,
                            "price": float(current_price),
                            "amount": float(net_sell_amount),  # 순 매도 금액
                            "fee": float(total_sell_fees),  # 총 매도 비용
                            "current_capital": float(current_capital),
                            "realized_pnl": float(realized_pnl),  # 실현 손익 (비용 반영)
                            "realized_pnl_pct": round(realized_pnl_pct, 2),  # 실현 수익률 (비용 반영)
                            "reason": f"{exit_reason} ({sell_count + 1}차 매도)",
                        })

                        # 매도 횟수 증가
                        sell_count += 1

                    # 포지션 완전 청산 시에만 상태 리셋
                    remaining_qty = curr_qty - sell_quantity
                    if remaining_qty <= 0:
                        # 완전 청산
                        entry_price = None
                        ema_breach_count = 0
                        prev_gap = None
                        sell_count = 0
                        buy_count = 0
                        first_sell_price = None
                    else:
                        # 1차 매도 후 포지션 남음
                        first_sell_price = current_price  # 1차 매도가 저장
                        entry_price = None  # 진입가 리셋 (재진입 대기)
                        ema_breach_count = 0
                        prev_gap = None
                        buy_count = 0  # 재진입 대기
                        # sell_count=1 유지 (2차 매도 위해)

            # === 2단계: 진입 신호 체크 (청산 후 포지션 재계산) ===
            # 청산 후 포지션 상태 다시 확인
            total_bought = sum(t["quantity"] for t in trades if t["action"] == "BUY")
            total_sold = sum(t["quantity"] for t in trades if t["action"] == "SELL")
            total_quantity = total_bought - total_sold
            has_position = total_quantity > 0

            # 포지션 없거나 2차 매수 가능한 경우
            can_buy = (not has_position and buy_count == 0) or (has_position and buy_count < 2)

            if can_buy and current_capital > 0:
                # 1차/2차 매수 조건 체크 분리
                if buy_count == 0:
                    # 1차 매수: 기본 진입 조건
                    entry_signal = self._check_entry_conditions(row)
                else:
                    # 2차 매수: 별도 조건 (더 엄격)
                    entry_signal = self._check_second_buy_conditions(row, entry_price, total_quantity)

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
                        buy_cost = executed_amount * self.COMMISSION_RATE  # 매수 수수료 적용
                        current_capital -= (executed_amount + buy_cost)  # 총 매수 금액 + 수수료 차감

                        # 1차/2차 매수 시 진입가 설정
                        if buy_count == 0:
                            # 1차 매수: 진입가 설정
                            entry_price = current_price
                            sell_count = 0  # 매도 횟수 리셋
                        else:
                            # 2차 매수: 평균 단가 재계산
                            curr_qty, curr_avg_cost = self._calculate_position_state(trades)
                            total_cost = (curr_avg_cost * curr_qty) + executed_amount
                            total_qty = curr_qty + buy_quantity
                            entry_price = total_cost / total_qty

                        entry_consecutive = 0
                        ema_breach_count = 0
                        prev_gap = None

                        # 매수 사유 상세화
                        if buy_count == 0:
                            reason = f"1차 매수 (OBV z={row['obv_z']:.2f}, 괴리율={row['gap_ratio']*100:.2f}%)"
                        else:
                            price_gain = (current_price - entry_price) / entry_price
                            reason = (f"2차 매수 (가격상승={price_gain*100:.2f}%, "
                                      f"OBV z={row['obv_z']:.2f}, 거래량={row['prdy_vol_ratio']*100:.0f}%)")

                        trades.append({
                            "date": current_date,
                            "action": "BUY",
                            "quantity": buy_quantity,
                            "price": float(current_price),
                            "amount": float(executed_amount),  # 매수 금액 (수수료 제외)
                            "fee": float(buy_cost),  # 매수 수수료
                            "current_capital": float(current_capital),
                            "reason": reason,
                        })

                        # 매수 횟수 증가
                        buy_count += 1

        # 백테스트 종료 시점에 보유 주식이 있는 경우, 최종일 종가로 평가하여 자본금에 합산
        total_bought = sum(t["quantity"] for t in trades if t["action"] == "BUY")
        total_sold = sum(t["quantity"] for t in trades if t["action"] == "SELL")
        remaining_qty = total_bought - total_sold

        final_capital = current_capital
        if remaining_qty > 0 and not eval_df.empty:
            last_row = eval_df.iloc[-1]
            last_price = last_row["STCK_CLPR"]
            gross_liquidation_value = remaining_qty * last_price
            liquidation_commission = gross_liquidation_value * self.COMMISSION_RATE
            liquidation_tax = gross_liquidation_value * self.TAX_RATE
            total_liquidation_fees = liquidation_commission + liquidation_tax
            net_liquidation_value = gross_liquidation_value - total_liquidation_fees

            final_capital += net_liquidation_value  # 순 청산 금액 합산

            _, current_avg_cost = self._calculate_position_state(trades)
            realized_pnl_before_fees = (last_price - current_avg_cost) * remaining_qty
            realized_pnl = realized_pnl_before_fees - total_liquidation_fees
            realized_pnl_pct = (realized_pnl / (current_avg_cost * remaining_qty)) * 100 if current_avg_cost * remaining_qty > 0 else 0.0

            trades.append({
                "date": last_row["STCK_BSOP_DATE"],
                "action": "LIQUIDATION",
                "quantity": remaining_qty,
                "price": float(last_price),
                "amount": float(net_liquidation_value),  # 순 청산 금액
                "fee": float(total_liquidation_fees),  # 총 청산 비용
                "current_capital": float(final_capital),
                "realized_pnl": float(realized_pnl),  # 실현 손익 (비용 반영)
                "realized_pnl_pct": round(realized_pnl_pct, 2),  # 실현 수익률 (비용 반영)
                "reason": "백테스트 종료, 최종 보유분 청산",
            })

        # 결과 포맷팅 (최종 평가 자본금 사용)
        result = self._format_result(prices_df, params, trades, final_capital)
        result["parameters"].update({
            "EMA_PERIOD": self.EMA_PERIOD,
            "MAX_SURGE_RATIO": self.MAX_SURGE_RATIO,
            "STOP_LOSS": self.STOP_LOSS_FIXED,
        })

        return result

    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """데이터 전처리"""
        if "STCK_BSOP_DATE" in df.columns:
            # 날짜 타입 변환
            df["STCK_BSOP_DATE"] = pd.to_datetime(df["STCK_BSOP_DATE"])
            df = df.sort_values("STCK_BSOP_DATE").reset_index(drop=True)

        for col in ["STCK_CLPR", "STCK_HGPR", "STCK_LWPR", "ACML_VOL"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술 지표 계산"""
        close = df["STCK_CLPR"].values.astype(float)
        high = df["STCK_HGPR"].values.astype(float)
        low = df["STCK_LWPR"].values.astype(float)
        volume = df["ACML_VOL"].values.astype(float)

        # EMA 20
        df["ema_20"] = pd.Series(ta.EMA(close, timeperiod=self.EMA_PERIOD), index=df.index)

        # ATR (변동성 지표)
        df["atr"] = pd.Series(ta.ATR(high, low, close, timeperiod=14), index=df.index)

        # ADX (추세 강도)
        df["adx"] = pd.Series(ta.ADX(high, low, close, timeperiod=14), index=df.index)
        df["plus_di"] = pd.Series(ta.PLUS_DI(high, low, close, timeperiod=14), index=df.index)
        df["minus_di"] = pd.Series(ta.MINUS_DI(high, low, close, timeperiod=14), index=df.index)

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

        # 거래량 비율 (실전 매매와 동일: 전일 대비)
        df["prdy_vol_ratio"] = df["ACML_VOL"] / df["ACML_VOL"].shift(1)

        return df

    def _check_entry_conditions(self, row: pd.Series) -> bool:
        """
        진입 조건 체크 (6개 조건)

        1. EMA 추세: 현재가 > EMA20
        2. 수급 강도: OBV z-score > 1.0 (거래량 강도 포함)
        3. 급등 필터: 당일 상승률 <= 5%
        4. 괴리율 필터: 괴리율 <= 5% (과열 방지)
        5. 추세 강도: ADX > 25 (횡보장 차단)
        6. 추세 방향: +DI > -DI (상승 추세 확인)
        """
        # NaN 체크
        if (pd.isna(row["ema_20"]) or pd.isna(row["obv_z"]) or pd.isna(row["gap_ratio"]) or
            pd.isna(row["adx"]) or pd.isna(row["plus_di"]) or pd.isna(row["minus_di"])):
            return False

        current_price = row["STCK_CLPR"]
        ema_20 = row["ema_20"]
        obv_z = row["obv_z"]
        gap_ratio = row["gap_ratio"]
        adx = row["adx"]
        plus_di = row["plus_di"]
        minus_di = row["minus_di"]
        daily_return = row["daily_return"] if not pd.isna(row["daily_return"]) else 0

        # 조건 1: EMA 추세
        price_above_ema = current_price > ema_20

        # 조건 2: 수급 강도 (OBV z-score)
        supply_strong = obv_z > self.OBV_Z_BUY_THRESHOLD

        # 조건 3: 급등 필터
        surge_filtered = daily_return <= self.MAX_SURGE_RATIO

        # 조건 4: 괴리율 과열 필터
        gap_filtered = gap_ratio <= self.MAX_GAP_RATIO

        # 조건 5: 추세 강도 (ADX > 25)
        trend_strong = adx > self.ADX_THRESHOLD

        # 조건 6: 추세 방향 (상승 추세)
        trend_upward = plus_di > minus_di

        return all([
            price_above_ema,
            supply_strong,
            surge_filtered,
            gap_filtered,
            trend_strong,
            trend_upward
        ])

    def _check_exit_conditions(
        self,
        row: pd.Series,
        prev_row: pd.Series,
        entry_price: float,
        ema_breach_count: int,
        prev_gap: Optional[float],
        sell_count: int,
        first_sell_price: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        청산 조건 체크 (1차/2차 매도 조건 분리)

        [1차 매도 조건]
        1. 고정 손절: -3%
        2. 수급 반전: OBV z-score < -1.0
        3. EMA-ATR 손절: 현재가 <= EMA - (ATR × 배수)
        4. 수급 약화: OBV 정체 + EMA 하회
        5. 추세 악화: EMA 아래 + 가격 하락 + 이탈폭 증가

        [2차 매도 조건]
        1. 1차 매도가 대비 -2% 추가 하락
        2. 수급 급락: OBV z-score < -1.5
        """
        current_price = row["STCK_CLPR"]
        ema_20 = row["ema_20"]
        atr = row["atr"]
        obv_z = row["obv_z"]

        # === 1차 매도 조건 ===
        if sell_count == 0:
            # NaN 체크 및 진입가 검증
            if entry_price is None or pd.isna(entry_price):
                return False, ""

            if pd.isna(ema_20) or pd.isna(obv_z) or pd.isna(atr):
                return False, ""

            profit_rate = (current_price - entry_price) / entry_price

            # 1. 고정 손절 -3% (백업)
            if profit_rate <= self.STOP_LOSS_FIXED:
                return True, f"고정 손절 ({profit_rate*100:.2f}%)"

            # 2. 수급 반전
            if obv_z <= self.OBV_Z_SELL_THRESHOLD:
                return True, f"수급 반전 (OBV z={obv_z:.2f})"

            # 3. EMA-ATR 동적 손절 (핵심!)
            ema_stop_loss = ema_20 - (atr * self.ATR_MULTIPLIER)
            if current_price <= ema_stop_loss:
                gap_pct = abs((current_price - ema_20) / ema_20) * 100
                return True, f"EMA-ATR 손절 (이탈 {gap_pct:.1f}%, ATR {atr:.0f})"

            # 4. 수급 약화 + EMA 하회
            below_ema = current_price < ema_20
            if abs(obv_z) < self.OBV_Z_WEAK_THRESHOLD and below_ema:
                return True, f"수급 약화 + EMA 하회 (OBV z={obv_z:.2f})"

            # 5. 추세 악화
            if below_ema and prev_gap is not None:
                current_gap = ema_20 - current_price
                prev_price = prev_row["STCK_CLPR"] if prev_row is not None else current_price
                price_declined = current_price < prev_price
                gap_increased = current_gap > prev_gap

                if price_declined and gap_increased:
                    return True, "추세 악화 (가격 하락 + 이탈폭 증가)"

        # === 2차 매도 조건 ===
        else:
            # 1차 매도가 대비 추가 하락만 체크
            if first_sell_price is None:
                return False, ""

            if pd.isna(obv_z):
                return False, ""

            additional_drop = (current_price - first_sell_price) / first_sell_price

            # 1. 1차 매도가 대비 -2% 추가 하락
            if additional_drop <= self.SECOND_SELL_DROP_THRESHOLD:
                return True, f"추가 하락 ({additional_drop*100:.2f}%)"

            # 2. 수급 급락
            if obv_z <= self.SECOND_SELL_OBV_THRESHOLD:
                return True, f"수급 급락 (OBV z={obv_z:.2f})"

        return False, ""

    def _check_second_buy_conditions(
        self,
        row: pd.Series,
        entry_price: float,
        hold_qty: int
    ) -> bool:
        """
        2차 매수 조건 체크 (일봉 버전)

        Conditions (all must pass):
        1. 가격 범위: 1차 매수가 대비 +1% ~ +4%
        2. EMA 위치: 현재가 > EMA20 (추세 확인)
        3. 수급 강도: OBV z-score >= 1.3 (1차보다 엄격, 거래량 강도 포함)
        4. 손절 안전거리: 현재가 >= 손절가 × 1.04 (4% 안전 마진)

        Args:
            row: 현재 데이터
            entry_price: 1차 매수가
            hold_qty: 보유 수량 (사용 안함)

        Returns:
            조건 충족 여부
        """
        # NaN 체크
        if pd.isna(row["ema_20"]) or pd.isna(row["obv_z"]):
            return False

        current_price = row["STCK_CLPR"]
        ema_20 = row["ema_20"]
        obv_z = row["obv_z"]

        # 1. 가격 범위 체크 (1차 매수가 대비 +1% ~ +4%)
        price_gain = (current_price - entry_price) / entry_price
        if price_gain < self.SECOND_BUY_PRICE_GAIN_MIN or price_gain > self.SECOND_BUY_PRICE_GAIN_MAX:
            return False

        # 2. EMA 위치 체크 (추세 확인)
        if current_price <= ema_20:
            return False

        # 3. 수급 강도 체크 (OBV z-score, 1차보다 엄격)
        if obv_z < self.SECOND_BUY_OBV_THRESHOLD:
            return False

        # 4. 손절 안전거리 체크 (현재가 >= 손절가 × 1.04)
        stop_loss_price = entry_price * (1 + self.STOP_LOSS_FIXED)  # -3% 손절가
        safety_threshold = stop_loss_price * (1 + self.SECOND_BUY_SAFETY_MARGIN)  # 손절가 위 4%

        if current_price < safety_threshold:
            return False

        return True
