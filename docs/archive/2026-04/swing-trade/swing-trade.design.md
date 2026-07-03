# swing-trade 공통 필터 개선 Design Document

> **Summary**: 매수 공통 필터의 전일 양봉 판단 → 전일 20EMA 상승 판단으로 교체
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-04-15
> **Status**: Draft
> **Plan Reference**: `docs/01-plan/features/swing-trade.plan.md`

---

## 1. 변경 개요

전일 양봉 판단(`yesterday_close >= yesterday_open`)을 전일 20EMA 상승 판단(`yesterday_ema20 > prev_ema20`)으로 교체한다. 20EMA 상승은 가격 추세의 방향성을 더 안정적으로 나타낸다.

---

## 2. 수정 상세

### 2.1 캐시 적재 — `app/domain/swing/service.py`

**위치**: ~L482 (`indicators_data` dict, 전전일 데이터 영역)

| 항목 | 변경 |
|------|------|
| 추가 필드 | `prev_ema20` |
| 값 | `float(prev_prev['ema_20'])` (NaN이면 None) |

```python
# 기존 (L482~485):
"prev_plus_di": float(prev_prev['plus_di']) if not pd.isna(prev_prev['plus_di']) else None,
"prev_minus_di": float(prev_prev['minus_di']) if not pd.isna(prev_prev['minus_di']) else None,
"prev_obv_z": float(prev_prev['obv_z']) if not pd.isna(prev_prev['obv_z']) else None,

# 변경 후:
"prev_ema20": float(prev_prev['ema_20']) if not pd.isna(prev_prev['ema_20']) else None,
"prev_plus_di": float(prev_prev['plus_di']) if not pd.isna(prev_prev['plus_di']) else None,
"prev_minus_di": float(prev_prev['minus_di']) if not pd.isna(prev_prev['minus_di']) else None,
"prev_obv_z": float(prev_prev['obv_z']) if not pd.isna(prev_prev['obv_z']) else None,
```

---

### 2.2 캐시 조회 — `app/domain/swing/trading/strategies/single_ema_strategy.py`

**위치**: `get_cached_indicators()` 반환 dict (~L81~83)

```python
# 기존:
'prev_plus_di': data.get('prev_plus_di'),
'prev_minus_di': data.get('prev_minus_di'),
'prev_obv_z': data.get('prev_obv_z'),

# 변경 후:
'prev_ema20': data.get('prev_ema20'),
'prev_plus_di': data.get('prev_plus_di'),
'prev_minus_di': data.get('prev_minus_di'),
'prev_obv_z': data.get('prev_obv_z'),
```

---

### 2.3 1차 매수 공통 필터 — `single_ema_strategy.py`

**위치**: `check_entry_signal()` (~L274~281)

```python
# Before:
yesterday_open = cached_indicators.get('open')         # 전일 시가
yesterday_close = cached_indicators.get('close')       # 전일 종가
yesterday_ema20 = cached_indicators.get('ema20')       # 전일 EMA20 (종가 기준)
yesterday_obv_z = cached_indicators.get('obv_z')       # 전일 OBV z-score

# === 공통 필터 ===
surge_filtered = abs(prdy_ctrt) / 100 <= cls.MAX_SURGE_RATIO
prev_day_bullish = (yesterday_open is not None and yesterday_close >= yesterday_open)

if not (surge_filtered and prev_day_bullish):

# After:
yesterday_ema20 = cached_indicators.get('ema20')       # 전일 EMA20 (종가 기준)
prev_ema20 = cached_indicators.get('prev_ema20')       # 전전일 EMA20
yesterday_obv_z = cached_indicators.get('obv_z')       # 전일 OBV z-score

# === 공통 필터 ===
surge_filtered = abs(prdy_ctrt) / 100 <= cls.MAX_SURGE_RATIO
ema20_rising = (yesterday_ema20 is not None and prev_ema20 is not None and yesterday_ema20 > prev_ema20)

if not (surge_filtered and ema20_rising):
```

> **Note**: `yesterday_open`, `yesterday_close` 변수는 이 블록에서 제거. 다른 곳에서 사용하지 않는지 확인 필요.

---

### 2.4 2차 매수 양봉 필터 — `single_ema_strategy.py`

**위치**: `check_second_buy_signal()` (~L388~392)

```python
# Before:
yesterday_open = cached_indicators.get('open')
yesterday_close = cached_indicators.get('close')
if not (yesterday_open is not None and yesterday_close >= yesterday_open):
    return None

# After:
yesterday_ema20 = cached_indicators.get('ema20')
prev_ema20 = cached_indicators.get('prev_ema20')
if not (yesterday_ema20 is not None and prev_ema20 is not None and yesterday_ema20 > prev_ema20):
    return None
```

---

## 3. 영향 범위 확인

| 위치 | `yesterday_open` 사용 | `yesterday_close` 사용 | 양봉 판단 |
|------|:---:|:---:|:---:|
| `check_entry_signal()` L274 | ✅ 제거 | ✅ 제거 | ✅ 변경 |
| `check_second_buy_signal()` L389 | ✅ 제거 | ✅ 제거 | ✅ 변경 |
| `check_exit_signal()` | 확인 필요 | 확인 필요 | - |

> 캐시 적재 시 `open`, `close` 필드 자체는 유지 (다른 전략/매도 로직에서 사용 가능)

---

## 4. Fallback 처리

배포 직후 기존 캐시에 `prev_ema20`가 없는 경우:

- `data.get('prev_ema20')` → `None` 반환
- `ema20_rising` 조건에서 `prev_ema20 is not None` 체크 → `False`
- **결과**: 공통 필터 미충족 → 매수 신호 차단
- 다음 일일 적재(15:31 `day_collect_job`) 이후 정상 동작

---

## 5. 구현 순서

1. `service.py` — `prev_ema20` 캐시 적재 추가
2. `single_ema_strategy.py` — `get_cached_indicators()` 반환 필드 추가
3. `single_ema_strategy.py` — `check_entry_signal()` 공통 필터 변경
4. `single_ema_strategy.py` — `check_second_buy_signal()` 양봉 필터 변경

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-15 | Initial design | 강일빈 |