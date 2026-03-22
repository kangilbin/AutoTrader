# Strategies Code Review Gap Analysis Report

> **Analysis Type**: Plan-Implementation Gap Analysis (Code Review Issues)
>
> **Project**: AutoTrader
> **Analyst**: gap-detector
> **Date**: 2026-02-28
> **Status**: Completed

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

5개 코드 리뷰 이슈(C-1, C-2, W-1, W-3, C-3)의 수정 계획(Plan)과 실제 구현(Implementation)을 비교하여, 각 이슈가 계획대로 정확히 반영되었는지 검증한다.

### 1.2 Analysis Scope

| File | Issues |
|------|--------|
| `app/domain/swing/trading/strategies/single_ema_strategy.py` | C-1, W-1, W-3 |
| `app/domain/swing/trading/strategies/base_trading_strategy.py` | C-2 |
| `app/domain/swing/service.py` | C-3 |

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| C-1: `.decode()` 제거 | 100% | PASS |
| C-2: SIGNAL 3 손절 체크 | 100% | PASS |
| W-1: ATR=0 가드 | 100% | PASS |
| W-3: 당일 급등 필터 | 100% | PASS |
| C-3: NaN 필터링 | 100% | PASS |
| **Overall Match Rate** | **100%** | **PASS** |

---

## 3. Issue-by-Issue Gap Analysis

---

### 3.1 C-1: `.decode()` 제거 -- Match

**Plan:**
```python
# Before (계획된 제거 대상)
intraday_low = float(intraday_low_str.decode())

# After (계획된 수정)
intraday_low = float(intraday_low_str)
```

**Implementation:** `single_ema_strategy.py` line 439
```python
intraday_low = float(intraday_low_str)
```

**Verification:**
- Redis 설정 확인: `app/common/redis.py` line 23에서 `decode_responses=True` 설정됨
- `.decode()` 호출이 파일 전체에서 완전히 제거됨
- `float(intraday_low_str)`로 직접 변환하여 `str -> float` 처리

**Side Effect 분석:**
- `decode_responses=True`이므로 Redis에서 반환하는 값은 항상 `str` 타입
- `float()` 함수는 `str` 타입을 정상적으로 처리하므로 부작용 없음
- 기존 코드에서 `.decode()`를 호출하면 `str` 객체에는 `decode()` 메서드가 없으므로 `AttributeError`가 발생했을 것

**Result:** Match -- 계획과 구현이 정확히 일치

---

### 3.2 C-2: SIGNAL 3에 손절 체크 추가 -- Match

**Plan:**
- SIGNAL 3 블록 시작 부분에 `check_exit_signal()` 호출
- `action == "SELL"` 시 `execute_second_sell()` (전량 매도)
- 성공 시 signal=0, entry_price=0, hold_qty=0, peak_price=0 리셋
- `TradeHistoryService.record_trade()` 호출
- 기존 재진입/EOD 로직은 `else` 블록으로 유지

**Implementation:** `base_trading_strategy.py` lines 664-819

SIGNAL 3 블록의 구현 구조:

```python
# line 664-665: 손절 체크 시작
elif current_signal == 3:
    # 손절 신호 체크 (SIGNAL 1/2와 동일)
    exit_result = None
    if entry_price > 0:
        exit_result = await cls.check_exit_signal(...)  # line 668-677

    # line 679: 손절 판단
    if exit_result and exit_result.get("action") == "SELL":
        # line 687: execute_second_sell 전량 매도
        order_result = await SwingOrderExecutor.execute_second_sell(...)

        if order_result.get("success"):
            # line 693-696: 상태 리셋
            new_signal = 0
            entry_price = 0
            hold_qty = 0
            peak_price = 0

            # line 699-705: 거래 내역 저장
            trade_service = TradeHistoryService(db)
            await trade_service.record_trade(...)

    else:
        # line 713-819: 기존 재진입/EOD 로직 (else 블록)
```

**Verification:**

| 계획 항목 | 구현 여부 | 위치 |
|-----------|:---------:|------|
| `check_exit_signal()` 호출 | PASS | line 668-677 |
| `entry_price > 0` 가드 조건 | PASS | line 667 |
| `execute_second_sell()` 전량 매도 | PASS | line 687-690 |
| signal=0 리셋 | PASS | line 693 |
| entry_price=0 리셋 | PASS | line 694 |
| hold_qty=0 리셋 | PASS | line 695 |
| peak_price=0 리셋 | PASS | line 696 |
| `TradeHistoryService.record_trade()` 호출 | PASS | line 699-705 |
| 기존 로직 `else` 블록 유지 | PASS | line 713 |

**SIGNAL 1/2와의 일관성 비교:**

| 비교 항목 | SIGNAL 1 (line 338-384) | SIGNAL 2 (line 533-579) | SIGNAL 3 (line 664-711) |
|-----------|:-----------------------:|:-----------------------:|:-----------------------:|
| `entry_price > 0` 가드 | PASS | PASS | PASS |
| `check_exit_signal()` | PASS | PASS | PASS |
| `execute_second_sell()` | PASS | PASS | PASS |
| 4항목 리셋 | PASS | PASS | PASS |
| `record_trade()` | PASS | PASS | PASS |
| user_id 검증 | PASS | PASS | PASS |
| 실패 로깅 | PASS | PASS | PASS |

**Side Effect 분석:**
- SIGNAL 3에서 손절이 트리거되면 `exit_result`가 truthy이므로 `else` 블록(재진입/EOD)이 스킵됨 -- 올바른 동작
- `exit_result = None` 초기화 후 `entry_price > 0`인 경우만 조회 -- `entry_price == 0`인 에지 케이스 안전 처리
- 리셋 후 signal=0이 되므로 다음 사이클에서 SIGNAL 0 브랜치로 진입 -- 정상 사이클 흐름

**Result:** Match -- 계획과 구현이 정확히 일치하며, SIGNAL 1/2 패턴과 일관성 유지

---

### 3.3 W-1: ATR=0 가드 -- Match

**Plan (2개 위치):**

**(A) `check_immediate_sell_signal`에서 ATR<=0이면 HOLD 반환:**
```python
if atr <= 0:
    logger.warning(f"[{symbol}] ATR이 0 이하, 손절 체크 스킵")
    return {"action": "HOLD", "reasons": []}
```

**(B) `check_second_buy_signal` 로그에서 ATR=0 시 ZeroDivisionError 방지:**
```python
atr_ratio = f"{(curr_price-realtime_ema20)/atr:.2f}" if atr > 0 else "N/A"
```

**Implementation:**

**(A)** `single_ema_strategy.py` lines 486-489:
```python
# ATR=0 가드: ATR이 유효하지 않으면 손절 체크 스킵
if atr <= 0:
    logger.warning(f"[{symbol}] ATR이 0 이하, 손절 체크 스킵")
    return {"action": "HOLD", "reasons": []}
```

**(B)** `single_ema_strategy.py` line 413:
```python
atr_ratio = f"{(curr_price-realtime_ema20)/atr:.2f}" if atr > 0 else "N/A"
```

**Verification:**

| 계획 항목 | 구현 여부 | 위치 |
|-----------|:---------:|------|
| `check_immediate_sell_signal` ATR<=0 가드 | PASS | line 487-489 |
| 경고 로그 출력 | PASS | line 488 |
| HOLD 반환 (dict 형식) | PASS | line 489 |
| `check_second_buy_signal` atr_ratio 안전 처리 | PASS | line 413 |

**Edge Case 분석:**
- ATR=0: `atr <= 0` 조건에 의해 HOLD 반환, `ema_atr_stop` 계산 자체를 스킵 -- 안전
- ATR<0: 동일하게 가드됨 (음수 ATR은 비정상 데이터)
- `check_second_buy_signal`에서 ATR=0: `trend_lower`, `pullback_lower` 등의 계산에서 `atr * 0.3 = 0`이 되어 가격 범위가 극도로 좁아짐 -- 사실상 매수 조건 자체가 성립하기 어려우므로 추가 가드 불필요 (현재 로그만 안전 처리)
- `check_immediate_sell_signal`에서 ATR=0이면 `ema_atr_stop = realtime_ema20`이 되어 EMA와 같은 값이 손절선이 됨 -- 가드 없으면 과도한 손절 발생 가능성 있었음

**Result:** Match -- 계획과 구현이 정확히 일치

---

### 3.4 W-3: 당일 급등 필터 추가 -- Match

**Plan:**
```python
intraday_surge_filtered = abs(prdy_ctrt) / 100 <= cls.MAX_SURGE_RATIO
```
그리고 `current_signal`의 `all()` 조건에 `intraday_surge_filtered` 추가

**Implementation:** `single_ema_strategy.py` lines 314, 319

```python
# line 314
intraday_surge_filtered = abs(prdy_ctrt) / 100 <= cls.MAX_SURGE_RATIO

# line 319
current_signal = all([price_above_ema, supply_strong, surge_filtered,
                      intraday_surge_filtered, trend_upward, ema_rising, prev_day_bullish])
```

**Verification:**

| 계획 항목 | 구현 여부 | 위치 |
|-----------|:---------:|------|
| `abs(prdy_ctrt) / 100 <= cls.MAX_SURGE_RATIO` | PASS | line 314 |
| `all()` 조건에 `intraday_surge_filtered` 포함 | PASS | line 319 |

**기존 필터와의 관계 분석:**
- `surge_filtered` (line 312-313): 전일 종가 대비 변동률 -- **과거** 데이터 기반 (어제 vs 그저께)
- `intraday_surge_filtered` (line 314): 당일 장중 상승률 -- **실시간** 데이터 기반 (오늘 현재)
- 두 필터는 상호 보완적: 과거 급등 + 장중 급등 모두 차단

**`MAX_SURGE_RATIO` 값 확인:**
- `base_single_ema.py` line 24: `MAX_SURGE_RATIO = 0.05` (5%)
- `abs(prdy_ctrt) / 100`: `prdy_ctrt`가 백분율(예: 5.0은 5%)이므로 `/100`으로 비율 변환 (0.05)
- `abs()` 사용으로 급락도 필터링 -- 양방향 급변 차단

**Side Effect 분석:**
- 기존 7개 조건(`all()`)에 1개 추가되어 8개 조건으로 확장
- 매수 조건이 더 엄격해짐 (의도된 동작)
- `prdy_ctrt = 0`인 경우: `0 / 100 = 0.0 <= 0.05` -- True, 정상 통과

**Result:** Match -- 계획과 구현이 정확히 일치

---

### 3.5 C-3: NaN 필터링 -- Match

**Plan (2개 위치):**
```python
recent_6_diffs = [x for x in obv_diffs.iloc[-7:-1].tolist() if not pd.isna(x)]
```

**(A)** `cache_single_indicators` (line ~295-297)
**(B)** `warmup_ema_cache` (line ~427-429)

**Implementation:**

**(A)** `service.py` lines 295-297:
```python
# OBV diff 최근 6일 추출 (NaN 필터링)
obv_diffs = indicators['obv'].diff()
recent_6_diffs = [x for x in obv_diffs.iloc[-7:-1].tolist() if not pd.isna(x)]
```

**(B)** `service.py` lines 427-429:
```python
# OBV diff 최근 6일 추출 (NaN 필터링)
obv_diffs = indicators['obv'].diff()
recent_6_diffs = [x for x in obv_diffs.iloc[-7:-1].tolist() if not pd.isna(x)]
```

**Verification:**

| 계획 항목 | 구현 여부 | 위치 |
|-----------|:---------:|------|
| `cache_single_indicators` NaN 필터링 | PASS | line 297 |
| `warmup_ema_cache` NaN 필터링 | PASS | line 429 |
| 두 위치 코드 동일 | PASS | 일관성 유지 |

**NaN 발생 시나리오 분석:**
- `indicators['obv'].diff()`의 첫 번째 요소는 항상 NaN (`diff()`는 이전 값과의 차이를 계산하므로 첫 값은 NaN)
- `iloc[-7:-1]`: 마지막 7번째부터 마지막 2번째까지 6개 요소 슬라이싱
- 데이터가 7일 미만인 경우 NaN이 포함될 수 있음
- 필터링 후 `recent_6_diffs`가 빈 리스트일 수 있으나, 상위 로직에서 `len(indicators) < 8` 가드가 있어 극단적 케이스 방어됨

**`warmup_ema_cache`에서 추가 확인:**
- line 446에서 `[float(x) for x in recent_6_diffs]`로 float 변환 -- NaN이 필터링된 상태이므로 안전
- `cache_single_indicators`에서는 line 314에서 `json.dumps(indicators_data)`로 직접 저장 -- NaN이 포함되면 JSON 직렬화 시 문제 발생 가능했으나, 필터링으로 방지됨

**Result:** Match -- 계획과 구현이 정확히 일치

---

## 4. Cross-Issue Consistency Analysis

### 4.1 SIGNAL 상태 머신 일관성

수정 후 전체 SIGNAL 흐름이 일관성을 유지하는지 검증:

```
SIGNAL 0 -- 1차 매수 --> SIGNAL 1
SIGNAL 1 -- 손절      --> SIGNAL 0 (전량 매도 + 리셋)     [기존]
SIGNAL 1 -- EOD 1차   --> SIGNAL 3 (분할 매도)            [기존]
SIGNAL 1 -- EOD 전량  --> SIGNAL 0 (전량 매도 + 리셋)     [기존]
SIGNAL 1 -- 2차 매수  --> SIGNAL 2                        [기존]
SIGNAL 2 -- 손절      --> SIGNAL 0 (전량 매도 + 리셋)     [기존]
SIGNAL 2 -- EOD 1차   --> SIGNAL 3 (분할 매도)            [기존]
SIGNAL 2 -- EOD 전량  --> SIGNAL 0 (전량 매도 + 리셋)     [기존]
SIGNAL 3 -- 손절      --> SIGNAL 0 (전량 매도 + 리셋)     [C-2 신규]
SIGNAL 3 -- 재진입    --> SIGNAL 1                        [기존, else 블록]
SIGNAL 3 -- EOD 전량  --> SIGNAL 0 (전량 매도 + 리셋)     [기존, else 블록]
```

- C-2 추가로 SIGNAL 3에서도 급락 시 즉시 손절 가능 -- 안전장치 강화
- 기존 재진입/EOD 로직은 `else` 블록으로 손절이 아닌 경우에만 실행 -- 우선순위 올바름

### 4.2 ATR 의존 로직 체인

W-1(ATR=0 가드)이 다른 ATR 사용 로직에 미치는 영향:

| 위치 | ATR 사용 | ATR=0 시 동작 | 안전성 |
|------|----------|---------------|:------:|
| `check_immediate_sell_signal` | `ema - (atr * 1.0)` | HOLD 반환 (가드) | PASS |
| `check_second_buy_signal` 시나리오 A | `ema + (atr * 0.3~2.0)` | 범위 = [ema, ema], 매우 좁음 | PASS (사실상 불성립) |
| `check_second_buy_signal` 시나리오 B | `ema - (atr * 0.5)` | 범위 = [ema, ema], 매우 좁음 | PASS (사실상 불성립) |
| `check_second_buy_signal` 로그 | `(price-ema)/atr` | "N/A" 출력 (가드) | PASS |
| `update_eod_signals_to_db` | ATR 미사용 | 해당 없음 | PASS |

### 4.3 데이터 파이프라인 일관성

C-3(NaN 필터링)이 데이터 소비 측에 미치는 영향:

```
[service.py] cache/warmup --> Redis (JSON)
    --> [single_ema_strategy.py] get_cached_indicators --> obv_recent_diffs
        --> [single_ema_strategy.py] get_realtime_obv_zscore
            --> TechnicalIndicators.calculate_realtime_obv_zscore(recent_6_diffs)
```

- `recent_6_diffs`에서 NaN이 제거된 상태로 Redis에 저장됨
- 소비 측(`calculate_realtime_obv_zscore`)에서는 clean 데이터를 받음 -- 안전
- 필터링 후 리스트 길이가 6 미만일 수 있으나, `calculate_realtime_obv_zscore` 내부에서 표준편차 계산 시 데이터 부족 처리가 필요할 수 있음 (현재 분석 범위 외)

---

## 5. Summary

```
+-----------------------------------------------+
|  Overall Match Rate: 100% (5/5 Issues)         |
+-----------------------------------------------+
|  C-1: .decode() 제거           Match (100%)    |
|  C-2: SIGNAL 3 손절 체크       Match (100%)    |
|  W-1: ATR=0 가드               Match (100%)    |
|  W-3: 당일 급등 필터           Match (100%)    |
|  C-3: NaN 필터링               Match (100%)    |
+-----------------------------------------------+
|  Side Effects:    None detected                |
|  Consistency:     Maintained                   |
|  Edge Cases:      Properly handled             |
+-----------------------------------------------+
```

---

## 6. Minor Observations (Not Issues)

아래 항목들은 이번 수정 계획의 범위 밖이지만, 검토 과정에서 발견한 참고 사항:

### 6.1 `check_second_buy_signal` ATR=0 추가 가드 고려

현재 `check_second_buy_signal`에서 ATR=0인 경우 시나리오 A/B의 가격 범위가 극도로 좁아져 사실상 조건 불성립이지만, 명시적 early return 가드를 추가하면 코드 의도가 더 명확해질 수 있다.

```python
# 현재: 암묵적으로 안전 (범위가 0에 수렴)
# 제안: 명시적 가드 (가독성 향상)
if atr <= 0:
    return None
```

### 6.2 `cache_single_indicators` vs `warmup_ema_cache` 코드 중복

`service.py`의 두 메서드(`cache_single_indicators` lines 241-347, `warmup_ema_cache` lines 349-496)에서 지표 계산 및 캐시 저장 로직이 거의 동일하다. 공통 로직을 private 메서드로 추출하면 향후 유지보수가 용이할 것이다.

### 6.3 `recent_6_diffs` 빈 리스트 가능성

NaN 필터링 후 `recent_6_diffs`가 빈 리스트(`[]`)가 될 수 있다. 하위 호출인 `calculate_realtime_obv_zscore`에서 빈 리스트에 대한 방어 로직이 있는지 별도 확인 권장.

---

## 7. Recommended Actions

### 7.1 Immediate: None Required

5개 이슈 모두 계획대로 정확히 구현되었으며, 부작용 없음.

### 7.2 Future Consideration

| Priority | Item | File | Notes |
|----------|------|------|-------|
| Low | `check_second_buy_signal` ATR=0 명시적 가드 | single_ema_strategy.py | 가독성 향상 목적 |
| Low | 캐싱 로직 중복 제거 | service.py | 리팩토링 범위 |
| Low | `recent_6_diffs` 빈 리스트 방어 확인 | indicators.py | 하위 모듈 점검 |

---

## 8. Additional Gap Analysis: 2026-03-13 Changes

> **Analysis Type**: Design-Implementation Gap Analysis (Feature Changes)
>
> **Date**: 2026-03-13
> **Status**: Completed

### 8.1 Analysis Purpose

2개 변경사항의 의도(Plan)와 실제 구현(Implementation)을 비교하여, 각 변경이 의도대로 정확히 반영되었는지 검증한다.

- **Change 1**: 단일 체결 시 거래 내역 DB 적재 (`order_executor.py`)
- **Change 2**: 직접 주문 API 엔드포인트 제거 (`order/router.py`)

### 8.2 Analysis Scope

| File | Change |
|------|--------|
| `app/domain/swing/trading/order_executor.py` | Change 1: 단일 체결 record_trade 추가 |
| `app/domain/order/router.py` | Change 2: POST /orders 엔드포인트 제거 |
| `app/domain/trade_history/service.py` | Cross-cutting: record_trade 인터페이스 호환성 |

### 8.3 Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Change 1: 단일 매수 체결 DB 저장 | 100% | PASS |
| Change 1: 단일 매도 체결 DB 저장 | 100% | PASS |
| Change 2: POST /orders 제거 | 100% | PASS |
| Change 2: 미사용 import 정리 | 100% | PASS |
| Cross-cutting: record_trade 호출 일관성 | 100% | PASS |
| **Overall Match Rate** | **100%** | **PASS** |

---

### 8.4 Change 1: 단일 체결 시 거래 내역 DB 적재 -- Match

#### 8.4.1 단일 매수 완료 분기 (`execute_buy_with_partial`)

**Plan:**
- `remaining_amount < curr_price` 분기(단일 매수 완료)에서 `TradeHistoryService.record_trade()` 호출 추가
- 분할 체결(`continue_partial_execution`)과 동일한 파라미터 형식

**Implementation:** `order_executor.py` lines 112-121

```python
# 거래 내역 DB 저장
from app.domain.trade_history import TradeHistoryService
trade_service = TradeHistoryService(db)
await trade_service.record_trade(
    swing_id=swing_id,
    trade_type="B",
    order_result={"qty": executed_qty, "avg_price": avg_price,
                  "order_no": order_no, "amount": executed_amount},
    reasons=[f"단일매수({signal_on_complete}차)", "100% 완료"]
)
```

**Verification:**

| 검증 항목 | 결과 | 비고 |
|-----------|:----:|------|
| `record_trade()` 호출 존재 | PASS | line 115-121 |
| `swing_id` 파라미터 전달 | PASS | 함수 인자에서 수신 |
| `trade_type="B"` (매수) | PASS | 분할 매수와 동일 |
| `order_result` dict 키: qty, avg_price, order_no, amount | PASS | 4개 키 모두 포함 |
| `reasons` 리스트 형식 | PASS | 2개 요소 |
| `TradeHistoryService(db)` db 전달 | PASS | 함수 인자 `db=None` 기본값이나 호출처에서 명시적 전달 |

#### 8.4.2 단일 매도 완료 분기 (`execute_sell_with_partial`)

**Plan:**
- `actual_qty >= target_qty` 분기(단일 매도 완료)에서 `TradeHistoryService.record_trade()` 호출 추가

**Implementation:** `order_executor.py` lines 189-198

```python
# 거래 내역 DB 저장
from app.domain.trade_history import TradeHistoryService
trade_service = TradeHistoryService(db)
await trade_service.record_trade(
    swing_id=swing_id,
    trade_type="S",
    order_result={"qty": actual_qty, "avg_price": avg_sell_price,
                  "order_no": order_no, "amount": actual_qty * avg_sell_price},
    reasons=[f"단일매도({signal_on_complete}차)", "100% 완료"]
)
```

**Verification:**

| 검증 항목 | 결과 | 비고 |
|-----------|:----:|------|
| `record_trade()` 호출 존재 | PASS | line 192-198 |
| `trade_type="S"` (매도) | PASS | 분할 매도와 동일 |
| `order_result` dict 키: qty, avg_price, order_no, amount | PASS | 4개 키 모두 포함 |
| `amount` 계산: `actual_qty * avg_sell_price` | PASS | 정수 곱셈, 정확한 금액 |

#### 8.4.3 분할 체결과의 일관성 비교

`continue_partial_execution` (기존)과 단일 체결 (신규)의 `record_trade` 호출을 비교:

| 비교 항목 | 분할 매수 (line 312-318) | 단일 매수 (line 115-121) | 일치 |
|-----------|------------------------|------------------------|:----:|
| `swing_id` | swing_id | swing_id | PASS |
| `trade_type` | "B" | "B" | PASS |
| `order_result.qty` | executed_qty | executed_qty | PASS |
| `order_result.avg_price` | avg_price | avg_price | PASS |
| `order_result.order_no` | order_no | order_no | PASS |
| `order_result.amount` | chunk_amount (float) | executed_amount (float) | PASS |
| `reasons` 형식 | `[f"분할매수(N차)", f"M% 완료"]` | `[f"단일매수(N차)", "100% 완료"]` | PASS (구분 가능) |

| 비교 항목 | 분할 매도 (line 368-374) | 단일 매도 (line 192-198) | 일치 |
|-----------|------------------------|------------------------|:----:|
| `swing_id` | swing_id | swing_id | PASS |
| `trade_type` | "S" | "S" | PASS |
| `order_result.qty` | actual_qty | actual_qty | PASS |
| `order_result.avg_price` | avg_sell_price | avg_sell_price | PASS |
| `order_result.order_no` | order_no | order_no | PASS |
| `order_result.amount` | `actual_qty * avg_sell_price` | `actual_qty * avg_sell_price` | PASS |
| `reasons` 형식 | `[f"분할매도(N차)", f"M% 완료"]` | `[f"단일매도(N차)", "100% 완료"]` | PASS (구분 가능) |

**`TradeHistoryService.record_trade()` 인터페이스 호환성:**

```python
# service.py line 26-31: record_trade 시그니처
async def record_trade(
    self,
    swing_id: int,        # -- 전달됨
    trade_type: str,       # -- "B" 또는 "S"
    order_result: dict,    # -- qty, avg_price, amount 키 사용 (line 60-62)
    reasons: Optional[list[str]] = None,  # -- 리스트 전달됨
) -> dict:
```

- `order_result`에서 사용하는 키: `avg_price` (line 60), `qty` (line 61), `amount` (line 62)
- 단일 체결에서 추가로 전달하는 `order_no` 키: 서비스에서 사용하지 않으나 무해 (dict.get 미참조)
- 분할 체결에서도 동일하게 `order_no`를 전달 중 -- 일관성 유지

**Result:** Match -- 단일 체결과 분할 체결의 record_trade 호출이 동일한 인터페이스를 사용하며 완전히 호환

---

### 8.5 Change 2: 직접 주문 API 엔드포인트 제거 -- Match

**Plan:**
- `POST /orders` (`create_order`) 엔드포인트 제거
- `OrderCreateRequest` import 제거
- 정정/취소 조회(GET /cancelable)와 정정/취소 실행(PATCH /{order_no})은 유지

**Implementation:** `order/router.py` (전체 42줄)

```python
from app.domain.order.schemas import OrderModifyRequest  # OrderCreateRequest 미포함

# 남은 엔드포인트:
# GET  /orders/cancelable  -- 정정/취소 가능 주문 조회
# PATCH /orders/{order_no} -- 주문 정정/취소
```

**Verification:**

| 검증 항목 | 결과 | 비고 |
|-----------|:----:|------|
| `POST /orders` 엔드포인트 제거 | PASS | `create_order` 함수 없음 |
| `OrderCreateRequest` import 제거 | PASS | line 11: `OrderModifyRequest`만 import |
| `GET /cancelable` 유지 | PASS | line 21-30 |
| `PATCH /{order_no}` 유지 | PASS | line 33-42 |
| `@router.post` 데코레이터 없음 | PASS | grep 결과 확인 |

**잔여 코드 분석:**

| 항목 | 위치 | 상태 | 영향도 |
|------|------|:----:|:------:|
| `OrderCreateRequest` 클래스 정의 | `order/schemas.py` line 8-12 | 존재 | 없음 (정의만 남음) |
| `OrderService.place_order()` 메서드 | `order/service.py` line 19 | 존재 | 없음 (호출처 제거됨) |
| `OrderCreateRequest` import in service | `order/service.py` line 8 | 존재 | 없음 (내부 사용) |

위 잔여 코드들은 router에서의 진입점이 제거되었으므로 HTTP API를 통한 직접 주문이 불가능하다. `OrderService.place_order()`와 `OrderCreateRequest`는 향후 정리 대상이나 현재 동작에 영향 없음.

**Result:** Match -- 엔드포인트가 완전히 제거되었고, 유지 대상 엔드포인트는 정상 보존

---

### 8.6 Cross-Cutting Analysis

#### 8.6.1 `db=None` 기본값 안전성

`execute_buy_with_partial`과 `execute_sell_with_partial` 모두 `db=None` 기본값을 가진다:

```python
async def execute_buy_with_partial(cls, ..., db=None) -> Dict[str, Any]:
async def execute_sell_with_partial(cls, ..., db=None) -> Dict[str, Any]:
```

**호출처 검증** (`base_trading_strategy.py`):

모든 호출에서 `db=db`를 명시적으로 전달하고 있음을 확인:
- line 278: `db=db`
- line 344: `db=db`
- (기타 모든 호출처 동일 패턴)

`db=None`인 채로 `TradeHistoryService(db)`가 호출되면 `AsyncSession` 대신 `None`이 전달되어 `self.repo = TradeHistoryRepository(None)` -- 이후 DB 쿼리 시 `AttributeError` 발생.

**현재 위험도**: 낮음. 모든 실제 호출처에서 db를 전달하고 있으며, `db=None`은 하위 호환성 기본값.

#### 8.6.2 Lazy Import 패턴

단일 체결 분기에서 `TradeHistoryService`를 함수 내부에서 import:

```python
# line 113 (매수), line 190 (매도)
from app.domain.trade_history import TradeHistoryService
```

`continue_partial_execution`에서는 메서드 상단에서 import:

```python
# line 243
from app.domain.trade_history import TradeHistoryService
```

**일관성**: 모두 lazy import 패턴 사용. 순환 import 방지를 위한 의도적 설계. 분기 내부 vs 메서드 상단 위치 차이가 있으나 동작에 영향 없음.

#### 8.6.3 거래 내역 저장 완전성 매트릭스

모든 주문 실행 경로에서 `record_trade`가 호출되는지 확인:

| 실행 경로 | record_trade 호출 | 상태 |
|-----------|:-----------------:|:----:|
| `execute_buy_with_partial` -- 단일 완료 | line 115-121 | PASS (신규) |
| `execute_buy_with_partial` -- 분할 시작 | 미호출 (첫 chunk) | 의도적 (continue에서 처리) |
| `execute_sell_with_partial` -- 단일 완료 | line 192-198 | PASS (신규) |
| `execute_sell_with_partial` -- 분할 시작 | 미호출 (첫 chunk) | 의도적 (continue에서 처리) |
| `continue_partial_execution` -- 매수 chunk | line 312-318 | PASS (기존) |
| `continue_partial_execution` -- 매도 chunk | line 368-374 | PASS (기존) |

**분할 시작(첫 chunk) 미저장 분석:**
- `execute_buy_with_partial`에서 분할이 시작되면(remaining_amount >= curr_price) Redis 상태만 저장하고 record_trade를 호출하지 않음
- 이후 `continue_partial_execution`의 매수 chunk 처리에서 누적 금액 기준으로 record_trade 호출
- 즉, 분할 첫 chunk의 거래 내역은 다음 사이클의 `continue_partial_execution`에서 누적 처리됨

이 패턴은 기존 설계와 동일하며, 이번 변경 범위에서는 "단일 완료" 경로만 추가 대상이었으므로 정확히 의도에 부합한다.

---

### 8.7 Summary (2026-03-13 Changes)

```
+-----------------------------------------------+
|  Overall Match Rate: 100% (2/2 Changes)        |
+-----------------------------------------------+
|  Change 1: 단일 매수 DB 저장    Match (100%)   |
|  Change 1: 단일 매도 DB 저장    Match (100%)   |
|  Change 2: POST /orders 제거    Match (100%)   |
+-----------------------------------------------+
|  record_trade 일관성: Maintained               |
|  Side Effects:       None detected             |
|  db=None 위험:       Low (호출처 안전)          |
+-----------------------------------------------+
```

---

### 8.8 Minor Observations (Not Issues)

#### 8.8.1 `OrderService.place_order()` 잔여 코드

`order/router.py`에서 `POST /orders`가 제거되었으나, `order/service.py`의 `place_order()` 메서드와 `order/schemas.py`의 `OrderCreateRequest` 클래스가 남아 있다. HTTP 진입점이 없어 외부에서 호출 불가능하지만, 데드코드 정리를 권장한다.

| Priority | Item | File | Notes |
|----------|------|------|-------|
| Low | `place_order()` 메서드 제거 또는 deprecated 표시 | order/service.py | 라우터에서 미사용 |
| Low | `OrderCreateRequest` 클래스 제거 검토 | order/schemas.py | 라우터 import에서 제거됨 |

#### 8.8.2 분할 체결 첫 chunk 거래 내역 누락

분할 매수/매도 시작 시 첫 chunk의 거래 내역은 즉시 저장되지 않고, 다음 사이클의 `continue_partial_execution`에서 누적 기록된다. 만약 첫 chunk 실행 후 시스템이 중단되면 해당 거래 내역이 유실될 수 있다. 현재 설계상 의도적이나, 장기적으로 첫 chunk도 즉시 기록하는 것을 고려할 수 있다.

---

### 8.9 Recommended Actions (2026-03-13)

#### 8.9.1 Immediate: None Required

2개 변경사항 모두 의도대로 정확히 구현되었으며, 부작용 없음.

#### 8.9.2 Future Consideration

| Priority | Item | File | Notes |
|----------|------|------|-------|
| Low | `place_order()` 데드코드 정리 | order/service.py | 라우터 진입점 제거 후 잔여 |
| Low | `OrderCreateRequest` 데드코드 정리 | order/schemas.py | 라우터 import에서 제거됨 |
| Low | 분할 첫 chunk 즉시 record_trade 고려 | order_executor.py | 시스템 중단 시 유실 방지 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-28 | Initial gap analysis for 5 code review issues | gap-detector |
| 2.0 | 2026-03-13 | Added analysis for single-execution DB recording and order endpoint removal | gap-detector |
