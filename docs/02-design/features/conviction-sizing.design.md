# Design: Conviction Score 기반 포지션 사이징

> Plan 문서: [conviction-sizing.plan.md](../../01-plan/features/conviction-sizing.plan.md)

## 변경 요약

매수 진입 시 OBV z-score와 ADX를 기반으로 Conviction Score(0.4~1.0)를 산출하여 투입 비율을 가변 조절한다.

```
기존: Qty = min(배정금 × 0.5 / 가격, 배정금 × 0.03 / 리스크)
변경: Qty = int(배정금 × 0.8 × Conviction / 가격)
```

## 상세 설계

### 1. BaseSingleEMAStrategy 파라미터 변경

**파일**: `app/domain/swing/trading/strategies/base_single_ema.py`

#### 제거

```python
# 제거
ENTRY_PCT = 0.5
MAX_LOSS_PCT = 0.03
```

#### 추가/변경

```python
# 포지션 사이징
# Qty = int(배정금 × MAX_ENTRY_PCT × conviction / 가격)
MAX_ENTRY_PCT = 0.8              # 최대 투입 비율 (배정금의 80%)
MIN_CONVICTION = 0.4             # 최소 확신도 (0.4 = 32% 투입)

# Conviction 가중치
CONVICTION_OBV_WEIGHT = 0.7      # OBV z-score 가중치 (70%)
CONVICTION_ADX_WEIGHT = 0.3      # ADX 가중치 (30%)
```

---

### 2. Conviction Score 계산 메서드

**파일**: `app/domain/swing/trading/strategies/base_single_ema.py`

BaseSingleEMAStrategy 클래스에 메서드 추가:

```python
@classmethod
def calc_conviction(cls, adx: float, obv_z: float) -> float:
    """
    매수 신호 강도 기반 확신도 계산 (0.4 ~ 1.0)
    
    - OBV z-score (70%): 수급 강도 → 추세 지속 가능성
    - ADX (30%): 추세 강도 (25 이상은 과열 감점)
    
    Returns:
        conviction: 0.4 (약한 신호) ~ 1.0 (매우 강한 신호)
    """
    # OBV 점수: z=0→0.3, z=0.5→0.53, z=1.0→0.77, z=1.5→1.0
    obv_score = max(0.3, min(1.0, 0.3 + obv_z * 0.467))

    # ADX 점수: 15→0.3, 20→0.65, 25→1.0, 30→0.8, 35→0.6 (과열 감점)
    if adx <= 25:
        adx_score = max(0.3, min(1.0, 0.3 + (adx - 15) * 0.07))
    else:
        adx_score = max(0.5, 1.0 - (adx - 25) * 0.04)

    raw = obv_score * cls.CONVICTION_OBV_WEIGHT + adx_score * cls.CONVICTION_ADX_WEIGHT

    return max(cls.MIN_CONVICTION, min(1.0, raw))
```

#### Conviction Score 매핑표

| OBV z | ADX | OBV 점수 | ADX 점수 | Raw | Conviction | 투입비 |
|-------|-----|---------|---------|-----|-----------|--------|
| 0.1 | 16 | 0.35 | 0.37 | 0.36 | **0.40** | 32% |
| 0.5 | 20 | 0.53 | 0.65 | 0.57 | **0.57** | 46% |
| 1.0 | 24 | 0.77 | 0.93 | 0.82 | **0.82** | 66% |
| 1.5 | 25 | 1.00 | 1.00 | 1.00 | **1.00** | 80% |
| 1.5 | 35 | 1.00 | 0.60 | 0.88 | **0.88** | 70% |

---

### 3. 백테스팅 전략 수량 계산 변경

**파일**: `app/domain/swing/backtest/strategies/single_ema_backtest_strategy.py`

#### 현재 코드 (lines 139-151)

```python
# Qty = min(배정금 × ENTRY_PCT / 현재가, 손실제한 수량)
entry_qty = int(current_capital * self.ENTRY_PCT / buy_price)

if pd.notna(atr) and atr > 0 and pd.notna(ema) and buy_price > 0:
    stop_price = ema - atr * self.ATR_MULTIPLIER
    risk_per_share = buy_price - stop_price
    if risk_per_share > 0:
        loss_limit_qty = int(current_capital * self.MAX_LOSS_PCT / risk_per_share)
        buy_quantity = min(entry_qty, loss_limit_qty)
    else:
        buy_quantity = entry_qty
else:
    buy_quantity = entry_qty
```

#### 변경 코드

```python
# Conviction 기반 포지션 사이징
adx = row.get("adx", 0) if pd.notna(row.get("adx", 0)) else 0
obv_z = row.get("obv_z", 0) if pd.notna(row.get("obv_z", 0)) else 0
conviction = self.calc_conviction(adx, obv_z)

# Qty = 배정금 × MAX_ENTRY_PCT × conviction / 가격
buy_quantity = int(current_capital * self.MAX_ENTRY_PCT * conviction / buy_price)
```

#### 변경 범위

- `entry_qty`, `loss_limit_qty`, `min()` 로직 전체 제거
- `self.ENTRY_PCT`, `self.MAX_LOSS_PCT` 참조 제거
- `calc_conviction()` 호출 추가
- 거래 기록에 conviction 값 포함 (디버깅용)

---

### 4. 실전 전략 수량 계산 변경

**파일**: `app/domain/swing/trading/auto_swing_batch.py`

#### 현재 코드 (lines 359-376)

```python
# 포지션 사이징: min(배정금 × ENTRY_PCT, 손실제한 수량)
realtime_ema20 = cached_indicators.get('realtime_ema20', 0)
realtime_atr = cached_indicators.get('realtime_atr', 0)
equity = float(swing.CUR_AMOUNT)
curr_price = float(current_price)

entry_qty = int(equity * strategy.ENTRY_PCT / curr_price) if curr_price > 0 else 0

stop_price = realtime_ema20 - realtime_atr * strategy.ATR_MULTIPLIER
risk_per_share = curr_price - stop_price

if risk_per_share > 0 and curr_price > 0:
    loss_limit_qty = int(equity * strategy.MAX_LOSS_PCT / risk_per_share)
    target_qty = min(entry_qty, loss_limit_qty)
else:
    target_qty = entry_qty
```

#### 변경 코드

```python
# Conviction 기반 포지션 사이징
realtime_adx = cached_indicators.get('realtime_adx', 0)
realtime_obv_z = cached_indicators.get('realtime_obv_z', 0)
equity = float(swing.CUR_AMOUNT)
curr_price = float(current_price)

conviction = strategy.calc_conviction(realtime_adx, realtime_obv_z)
target_qty = int(equity * strategy.MAX_ENTRY_PCT * conviction / curr_price) if curr_price > 0 else 0

logger.info(f"[{st_code}] 포지션 사이징: conviction={conviction:.2f}, "
            f"투입비={strategy.MAX_ENTRY_PCT * conviction:.1%}, 수량={target_qty}")
```

#### 변경 범위

- `entry_qty`, `loss_limit_qty`, `min()`, `stop_price`, `risk_per_share` 로직 전체 제거
- `realtime_ema20`, `realtime_atr` 참조 제거 (수량 계산 목적으로는 불필요, 손절은 별도)
- `realtime_adx`, `realtime_obv_z`는 이미 `cached_indicators`에 존재 (추가 API 호출 없음)
- conviction 로그 추가

---

### 5. 실전 전략 check_entry_signal 반환값 변경 (불필요)

`check_entry_signal`은 현재 `{'action': 'BUY', 'price': ..., 'reasons': [...]}` 반환.
`_handle_waiting`에서 conviction을 별도 계산하므로 **반환값 변경 불필요**.
`cached_indicators`에 이미 `realtime_adx`, `realtime_obv_z`가 포함되어 있음.

---

### 6. 데이터 해상도에 따른 차이

| 항목 | 백테스팅 | 실전 |
|------|---------|------|
| ADX 소스 | `row["adx"]` (전일 종가 기준) | `realtime_adx` (실시간 증분) |
| OBV z 소스 | `row["obv_z"]` (전일 종가 기준) | `realtime_obv_z` (실시간 증분) |
| 계산 위치 | `single_ema_backtest_strategy.py` 매수 블록 | `auto_swing_batch.py` `_handle_waiting` |
| conviction 로그 | 거래 기록 dict에 포함 | logger.info |

**공통**: `calc_conviction()` 메서드는 `base_single_ema.py`에 1곳만 존재. 양쪽 동일 로직.

---

## 구현 순서

1. `base_single_ema.py` — ENTRY_PCT→MAX_ENTRY_PCT 변경, MAX_LOSS_PCT 제거, calc_conviction() 추가
2. `single_ema_backtest_strategy.py` — 수량 계산 로직 변경
3. `auto_swing_batch.py` — 수량 계산 로직 변경
4. 백테스트 README 업데이트
5. 실전전략 README 업데이트
