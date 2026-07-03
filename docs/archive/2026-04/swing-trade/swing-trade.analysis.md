# swing-trade Gap Analysis Report

> **Feature**: swing-trade (공통 필터 개선)
> **Date**: 2026-04-15
> **Match Rate**: 100%
> **Design**: `docs/02-design/features/swing-trade.design.md`

---

## 1. Design Changes (4/4 Match)

| # | Change | Design | Implementation | Status |
|:-:|--------|--------|----------------|:------:|
| 1 | `prev_ema20` 캐시 적재 | Section 2.1 | `service.py` L483 | Match |
| 2 | `get_cached_indicators()` 반환 필드 | Section 2.2 | `single_ema_strategy.py` L81 | Match |
| 3 | 1차 매수 `prev_day_bullish` → `ema20_rising` | Section 2.3 | `single_ema_strategy.py` L275-283 | Match |
| 4 | 2차 매수 양봉 필터 → EMA 상승 필터 | Section 2.4 | `single_ema_strategy.py` L389-392 | Match |

---

## 2. Verification Items (6/6 Pass)

| # | Item | Result |
|:-:|------|--------|
| V1 | `yesterday_open` 매수 신호에서 제거 | strategies/ 내 0건 |
| V2 | `yesterday_close` OBV 계산에 유지 | L204, L208에서 사용 |
| V3 | 캐시 `close` 필드 유지 | service.py L476, strategy L74 |
| V4 | 캐시 `open` 필드 유지 | service.py L477, strategy L75 |
| V5 | `data.get('prev_ema20')` graceful None | L81 `.get()` 사용 |
| V6 | `prev_ema20 is None` 시 필터 차단 | L281 None 가드 확인 |

---

## 3. Additional Findings (Design Scope 외)

### 3.1 백테스트 전략 미동기화 (Medium)

`app/domain/swing/backtest/strategies/single_ema_backtest_strategy.py`에서 기존 양봉 판단 로직이 그대로 남아있음:
- L256-257: `prev_day_bullish = prev_row["STCK_CLPR"] >= prev_row["STCK_OPRC"]`
- L297-299: `if not (prev_row["STCK_CLPR"] >= prev_row["STCK_OPRC"])`

백테스트 결과가 실전 매매와 다르게 동작할 수 있음.

---

## 4. Overall Score

| Category | Score |
|----------|:-----:|
| Design Match | 100% |
| Architecture Compliance | 100% |
| Convention Compliance | 100% |
| **Overall** | **100%** |

---

## 5. Recommended Actions

| Priority | Item | Location |
|----------|------|----------|
| Medium | 백테스트 전략도 `ema20_rising`으로 동기화 | `single_ema_backtest_strategy.py` L256-257, L297-299 |
