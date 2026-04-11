# foreign-swing Gap Analysis Report

> **Feature**: foreign-swing
> **Date**: 2026-04-11
> **Design Doc**: [foreign-swing.design.md](../02-design/features/foreign-swing.design.md)

---

## 1. Overall Score

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall Match Rate** | **100%** | **PASS** |

---

## 2. Detailed Analysis

### 2.1 Core Implementation Items (stock_data_batch.py)

| # | Design Spec | Implementation | Status | Evidence |
|---|------------|----------------|--------|----------|
| I-01 | `from app.external import foreign_api` (line 11) | Exists at line 11 | MATCH | `stock_data_batch.py:11` |
| I-02 | `process_date_range` 내 `mrkt_code == "NASD"` 분기 | if/else branch at lines 60-73 | MATCH | `stock_data_batch.py:60-73` |
| I-03 | 나머지 코드(DB 저장, 지표 캐싱) 변경 없음 | DB 저장(lines 97-127), 상태 업데이트(line 130) 모두 보존 | MATCH | 분기 외 diff 없음 |

Design Section 4.1 "After" 코드와 실제 구현이 **라인 단위로 일치**:
- `if mrkt_code == "NASD":` 조건 -- 일치
- `foreign_api.get_stock_data(user_id, st_code, start, end, db)` 시그니처 -- 일치
- `else:` 분기의 기존 `get_stock_data()` -- 일치

### 2.2 "Already Resolved" Items Verification

| # | Item | Design Reference | Verified At | Status |
|---|------|-----------------|-------------|--------|
| R-01 | 응답 필드 매핑 (`open->STCK_OPRC` 등) | `foreign_api.py:289-306` | `column_mapping` dict 6개 매핑 확인 | MATCH |
| R-02 | 일일 데이터 수집 해외 분기 | `auto_swing_batch.py:852-904` | `_overseas` 분기 + `foreign_api.get_target_price()` 확인 | MATCH |
| R-03 | 스케줄러 등록 | `scheduler.py:50-85` | `us_ema_cache_warmup_job`(22:00), `us_trade_job`(23:00-05:25), `us_day_collect_job`(06:35) 등록 확인 | MATCH |
| R-04 | 배치 매매 분기 | `auto_swing_batch.py:968-990` | `us_trade_job()` -> `get_active_overseas_swings()` 호출 확인 | MATCH |
| R-05 | 잔고 조회 분기 | `swing/service.py:155-162` | `mrkt_code == "NASD"` -> `foreign_api.get_stock_balance()` 확인 | MATCH |
| R-06 | 주문 실행 분기 | `trading/order_executor.py` | 4개 함수에서 `foreign_api.place_order_api()` 분기 확인 | MATCH |

### 2.3 E2E Flow Verification (Design Section 5.2)

| # | Checklist Item | Code Path | Status |
|---|---------------|-----------|--------|
| E-01 | NASD 스윙 등록 시 `foreign_api.get_stock_data()` 호출 | `stock_data_batch.py:60-66` | PASS |
| E-02 | 3년치 데이터 STOCK_DAY_HISTORY에 올바른 필드로 저장 | `foreign_api.py:288-306` 정규화 -> `stock_data_batch.py:104-116` 저장 | PASS |
| E-03 | `cache_single_indicators()` 정상 동작 | 정규화된 필드(`STCK_CLPR` 등)가 국내와 동일 — 캐싱 로직 호환 | PASS |
| E-04 | 국내(J) 스윙 등록 시 기존 `kis_api.get_stock_data()` 유지 | `stock_data_batch.py:67-73` else 분기 | PASS |

---

## 3. Match Rate Summary

```
Total items checked:       13
  Core Implementation:     3/3   (100%)
  Already Resolved:        6/6   (100%)
  E2E Verification:        4/4   (100%)

Missing in Design:         0
Missing in Implementation: 0
Changed from Design:       0
```

---

## 4. Gaps Found

**None.** Design과 구현이 완전히 일치합니다.

---

## 5. Change Footprint

| Metric | Value |
|--------|-------|
| 추가된 라인 | +7줄 (import 1줄 + if/else 6줄) |
| 삭제된 라인 | 0줄 |
| 변경 파일 수 | 1개 (`stock_data_batch.py`) |
| Design 예상 변경량 | ~7줄 -- **정확히 일치** |
