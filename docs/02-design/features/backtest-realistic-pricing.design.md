# backtest-realistic-pricing Design Document

> **Summary**: 백테스팅 체결가를 종가/저가 대신 실제 신호 발생 가격으로 변경하여 실전 매매와의 괴리 해소
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-05-10
> **Status**: Draft
> **Planning Doc**: [backtest-realistic-pricing.plan.md](../../01-plan/features/backtest-realistic-pricing.plan.md)

---

## 1. Overview

### 1.1 Design Goals

`SingleEMABacktestStrategy.compute()`에서 매수/매도 체결가를 **신호 트리거 가격**으로 변경한다. 일봉 데이터의 [저가, 고가] 범위 안에 신호 가격이 존재하면 해당 가격으로 체결 처리하여, 실전 매매와 유사한 백테스팅 결과를 도출한다.

### 1.2 Design Principles

- **최소 변경**: `single_ema_backtest_strategy.py` 내부 로직만 수정, 외부 인터페이스 변경 없음
- **안전한 fallback**: 신호 가격이 [저가, 고가] 범위 밖이면 기존 종가/저가 사용
- **손절 로직 유지**: 이미 `ema_atr_stop` 가격으로 올바르게 구현되어 있으므로 변경 없음

---

## 2. Architecture

### 2.1 현재 체결가 흐름 (문제)

```
[매수]
  _check_entry_conditions() → matched=True
  buy_price = row["STCK_CLPR"]  ← 종가 사용 (문제)
  
[1차 익절]
  stop_price = peak_price - ATR × 2.0
  row["STCK_LWPR"] ≤ stop_price → 트리거
  sell_price = row["STCK_CLPR"]  ← 종가 사용 (문제)

[2차 익절]
  stop_price = peak_price - ATR × 2.0
  row["STCK_LWPR"] ≤ stop_price AND OBV z-score < -0.5 → 트리거
  sell_price = row["STCK_CLPR"]  ← 종가 사용 (문제)

[손절]
  ema_atr_stop = EMA20 - ATR × 1.0
  row["STCK_LWPR"] ≤ ema_atr_stop → 트리거
  sell_price = ema_atr_stop  ← ✅ 이미 올바름
```

### 2.2 변경 후 체결가 흐름

```
[매수]
  _check_entry_conditions() → matched=True, signal_price=EMA20
  저가 ≤ signal_price ≤ 고가 → buy_price = signal_price
  범위 밖 → buy_price = STCK_CLPR (fallback)

[1차 익절]
  stop_price = peak_price - ATR × 2.0
  저가 ≤ stop_price → 트리거
  sell_price = stop_price  ← 트리거 가격으로 매도

[2차 익절]
  stop_price = peak_price - ATR × 2.0
  저가 ≤ stop_price AND OBV z-score < -0.5 → 트리거
  sell_price = stop_price  ← 트리거 가격으로 매도

[손절]
  (변경 없음 — 이미 ema_atr_stop으로 체결)
```

---

## 3. Detailed Design

### 3.1 `_check_entry_conditions()` 반환값 확장

**변경 전:**
```python
def _check_entry_conditions(self, row, prev_row, i_minus_2) -> Tuple[bool, List[str]]:
    ...
    return True, ["눌림목매집", "EMA근접", "OBV양호", "추세상승"]
    ...
    return True, ["EMA돌파", "상향돌파", "추세확인", "거래량동반"]
    ...
    return False, []
```

**변경 후:**
```python
def _check_entry_conditions(self, row, prev_row, i_minus_2) -> Tuple[bool, List[str], float]:
    ...
    signal_price = row["ema_20"]
    return True, ["눌림목매집", "EMA근접", "OBV양호", "추세상승"], signal_price
    ...
    signal_price = row["ema_20"]
    return True, ["EMA돌파", "상향돌파", "추세확인", "거래량동반"], signal_price
    ...
    return False, [], 0.0
```

**signal_price 결정 로직:**
- **시나리오 A (눌림목)**: `EMA20` — EMA 근접 매수이므로 EMA 값이 매수 시점 추정가
- **시나리오 B (돌파)**: `EMA20` — EMA 돌파 시점의 가격

> 두 시나리오 모두 EMA20을 기준으로 매수 신호가 발생하므로 `ema_20`이 가장 합리적인 추정가.

### 3.2 `compute()` 매수 로직 변경

**변경 전 (line 105~108):**
```python
matched, signal_reasons = self._check_entry_conditions(row, prev_row, prev_prev_row)

if matched:
    buy_price = row["STCK_CLPR"]
```

**변경 후:**
```python
matched, signal_reasons, signal_price = self._check_entry_conditions(row, prev_row, prev_prev_row)

if matched:
    # 신호 가격이 당일 [저가, 고가] 범위 안이면 해당 가격으로 체결
    if signal_price > 0 and row["STCK_LWPR"] <= signal_price <= row["STCK_HGPR"]:
        buy_price = signal_price
    else:
        buy_price = row["STCK_CLPR"]  # fallback
```

### 3.3 `compute()` 1차 익절 로직 변경

**변경 전 (line 80~88):**
```python
if signal == 1:
    sell_qty = hold_qty // 2
    if sell_qty > 0:
        current_capital = self._execute_partial_sell(
            trades, current_date, row["STCK_CLPR"], sell_qty,
            current_capital,
            ["1차익절", f"고점대비 -{drawdown_pct}%"]
        )
        hold_qty -= sell_qty
        signal = 2
        peak_price = row["STCK_CLPR"]  # PEAK 리셋
```

**변경 후:**
```python
if signal == 1:
    sell_qty = hold_qty // 2
    if sell_qty > 0:
        current_capital = self._execute_partial_sell(
            trades, current_date, stop_price, sell_qty,
            current_capital,
            ["1차익절", f"고점대비 -{drawdown_pct}%"]
        )
        hold_qty -= sell_qty
        signal = 2
        peak_price = stop_price  # PEAK 리셋도 stop_price로
```

### 3.4 `compute()` 2차 익절 로직 변경

**변경 전 (line 94~100):**
```python
elif signal == 2:
    obv_z = row.get("obv_z", 0)
    if pd.notna(obv_z) and obv_z < self.OBV_Z_SELL_THRESHOLD:
        current_capital = self._execute_sell(
            trades, current_date, row["STCK_CLPR"],
            current_capital,
            ["2차익절", f"고점대비 -{drawdown_pct}%", f"OBV z={obv_z:.2f}"]
        )
```

**변경 후:**
```python
elif signal == 2:
    obv_z = row.get("obv_z", 0)
    if pd.notna(obv_z) and obv_z < self.OBV_Z_SELL_THRESHOLD:
        current_capital = self._execute_sell(
            trades, current_date, stop_price,
            current_capital,
            ["2차익절", f"고점대비 -{drawdown_pct}%", f"OBV z={obv_z:.2f}"]
        )
```

### 3.5 변경 요약 매트릭스

| 항목 | 변경 전 체결가 | 변경 후 체결가 | 비고 |
|------|--------------|--------------|------|
| 매수 | `STCK_CLPR` | `EMA20` (범위 검증) | fallback: 종가 |
| 손절 | `ema_atr_stop` | 변경 없음 | ✅ 이미 올바름 |
| 1차 익절 | `STCK_CLPR` | `stop_price` (PEAK-ATR×2.0) | |
| 2차 익절 | `STCK_CLPR` | `stop_price` (PEAK-ATR×2.0) | |
| 1차 익절 후 peak 리셋 | `STCK_CLPR` | `stop_price` | |

---

## 4. Implementation Order

| # | 작업 | 파일 | 변경 범위 |
|---|------|------|----------|
| 1 | `_check_entry_conditions()` 반환값에 `signal_price` 추가 | `single_ema_backtest_strategy.py` | 반환 타입 + return 문 4곳 |
| 2 | `compute()` 매수 체결가 → signal_price | 동일 | line 105~108 |
| 3 | `compute()` 1차 익절 체결가 → stop_price | 동일 | line 80~88 |
| 4 | `compute()` 2차 익절 체결가 → stop_price | 동일 | line 94~100 |
| 5 | 백테스팅 전략 README 업데이트 | `backtest/strategies/README.md` | 체결가 설명 변경 |

---

## 5. Edge Cases

| 케이스 | 처리 방식 |
|--------|----------|
| signal_price가 0 또는 NaN | 종가(STCK_CLPR)로 fallback |
| signal_price > 고가 | 종가로 fallback (장중에 도달 불가) |
| signal_price < 저가 | 종가로 fallback (장중에 도달 불가) |
| stop_price > 고가 | trailing stop 조건 미충족 (기존 로직이 이미 저가 ≤ stop_price로 필터) |
| ATR이 0인 경우 | trailing stop 스킵 (기존 로직 유지) |

---

## 6. Impact Analysis

### 6.1 변경 영향
- **매수가**: EMA20 ≈ 종가 근처이므로 대부분 소폭 차이, 하지만 급등일에는 유의미한 차이
- **익절 매도가**: stop_price ≥ 종가인 경우 수익률 개선 (고점에서 내려올 때 종가보다 높은 가격에 매도)
- **기존 API 인터페이스**: 변경 없음 (내부 로직만 변경)
- **다른 전략 (EMA, Ichimoku)**: 영향 없음 (SingleEMABacktest만 변경)

### 6.2 리스크
- 백테스팅 수익률이 기존 대비 변동됨 → 기존 결과와 직접 비교 시 주의 필요
