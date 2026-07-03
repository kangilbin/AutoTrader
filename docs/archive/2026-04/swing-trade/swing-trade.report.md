# swing-trade Completion Report

> **Feature**: swing-trade (공통 필터 개선)
> **Date**: 2026-04-15
> **Match Rate**: 100%
> **Iteration Count**: 0
> **Status**: Completed

---

## 1. Summary

매수 신호 공통 필터의 전일 양봉 판단(`yesterday_close >= yesterday_open`)을 전일 20EMA 상승 판단(`yesterday_ema20 > prev_ema20`)으로 교체했다. 시가/종가 비교는 장중 변동성에 의해 부정확할 수 있으나, 20EMA 상승은 가격 추세의 방향성을 평활화하여 더 안정적인 판단이 가능하다.

---

## 2. Changes Overview

### 2.1 실전 전략 (3개 파일, 4개 수정 포인트)

| # | 파일 | 변경 내용 |
|---|------|-----------|
| 1 | `app/domain/swing/service.py` L483 | 캐시 적재 시 `prev_ema20`(전전일 EMA20) 필드 추가 |
| 2 | `app/domain/swing/trading/strategies/single_ema_strategy.py` L81 | `get_cached_indicators()`에 `prev_ema20` 반환 추가 |
| 3 | `single_ema_strategy.py` L275-283 | 1차 매수 공통 필터: `prev_day_bullish` → `ema20_rising` |
| 4 | `single_ema_strategy.py` L388-392 | 2차 매수 필터: 동일하게 EMA 상승 판단으로 변경 |

### 2.2 백테스트 전략 동기화 (1개 파일, 5개 수정 포인트)

| # | 파일 | 변경 내용 |
|---|------|-----------|
| 5 | `single_ema_backtest_strategy.py` L240 | `_check_entry_conditions` 시그니처에 `i_minus_2` 추가 |
| 6 | `single_ema_backtest_strategy.py` L256-261 | 1차 매수 양봉 필터 → EMA20 상승 필터 |
| 7 | `single_ema_backtest_strategy.py` L292 | `_check_second_buy_conditions` 시그니처에 `i_minus_2` 추가 |
| 8 | `single_ema_backtest_strategy.py` L300-305 | 2차 매수 양봉 필터 → EMA20 상승 필터 |
| 9 | `single_ema_backtest_strategy.py` L99, L120, L125 | 호출부 3곳에 `prev_prev_row` 전달 |

### 2.3 제거된 코드

- 1차 매수 블록에서 `yesterday_open`, `yesterday_close` 변수 제거 (양봉 판단에만 사용)
- 2차 매수 블록에서 `yesterday_open`, `yesterday_close` 변수 제거
- OBV 실시간 계산(L204)의 `yesterday_close`는 별도 블록이므로 영향 없음

---

## 3. Before / After

### 실전 전략 — 공통 필터

```python
# Before (L274-281):
yesterday_open = cached_indicators.get('open')
yesterday_close = cached_indicators.get('close')
prev_day_bullish = (yesterday_open is not None and yesterday_close >= yesterday_open)

# After (L275-281):
yesterday_ema20 = cached_indicators.get('ema20')
prev_ema20 = cached_indicators.get('prev_ema20')
ema20_rising = (yesterday_ema20 is not None and prev_ema20 is not None and yesterday_ema20 > prev_ema20)
```

### 백테스트 전략 — 공통 필터

```python
# Before (L256):
prev_day_bullish = prev_row["STCK_CLPR"] >= prev_row["STCK_OPRC"]

# After (L256-261):
if i_minus_2 is not None and not pd.isna(i_minus_2.get("ema_20")):
    if not (prev_row["ema_20"] > i_minus_2["ema_20"]):
        return False, []
```

---

## 4. Gap Analysis Result

- **Match Rate**: 100% (4/4 설계 항목 일치, 6/6 검증 통과)
- **Iteration**: 0회 (1차에 통과)
- **백테스트 동기화**: Gap 분석 후 추가 요청으로 완료

---

## 5. Fallback & Risk

| 항목 | 처리 |
|------|------|
| 배포 직후 캐시에 `prev_ema20` 없음 | `.get()` → None → 필터 차단 → 다음 일일 적재(15:31) 후 정상 |
| 신규 상장 종목 전전일 EMA20 NaN | NaN 체크 후 None 처리 |
| 캐시 `open`/`close` 필드 | 유지 (OBV 계산 등 다른 용도 사용) |

---

## 6. PDCA Timeline

| Phase | Status | Date |
|-------|--------|------|
| Plan | Completed | 2026-04-15 |
| Design | Completed | 2026-04-15 |
| Do | Completed | 2026-04-15 |
| Check | 100% | 2026-04-15 |
| Report | Completed | 2026-04-15 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-04-15 | Completion report | 강일빈 |
