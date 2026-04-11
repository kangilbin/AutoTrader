# foreign-swing Design Document

> **Summary**: 스윙 등록 시 MRKT_CODE 기반 국내/해외 API 자동 분기 — 3년치 데이터 적재 경로 해외 지원
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-04-11
> **Status**: Draft
> **Planning Doc**: [foreign-swing.plan.md](../01-plan/features/foreign-swing.plan.md)

---

## 1. Overview

### 1.1 Design Goals

`stock_data_batch.py`의 `fetch_and_store_3_years_data()` 함수에서 `mrkt_code`에 따라 국내/해외 API를 분기하여, NASD 종목 스윙 등록 시 해외 API로 3년치 데이터를 정상 적재한다.

### 1.2 Design Principles

- **최소 변경**: 기존 동작 코드를 변경하지 않고 분기만 추가
- **기존 패턴 준수**: 프로젝트에서 이미 사용 중인 `_overseas = mrkt_code == "NASD"` 분기 패턴 활용
- **정규화 레이어 활용**: `foreign_api.get_stock_data()`가 이미 응답 필드를 DB 스키마(`STCK_OPRC` 등)로 변환하므로, 호출부 변경 불필요

---

## 2. Architecture

### 2.1 현재 흐름 (문제)

```
POST /swing (MRKT_CODE=NASD)
    ↓
SwingService.create_swing()
    ↓ DATA_YN != 'Y'
    ↓
_fetch_and_cache()
    ↓
fetch_and_store_3_years_data()
    ↓
kis_api.get_stock_data()  ← 항상 국내 API 호출 (NASD도!)
```

### 2.2 변경 후 흐름

```
POST /swing (MRKT_CODE=NASD)
    ↓
SwingService.create_swing()
    ↓ DATA_YN != 'Y'
    ↓
_fetch_and_cache()
    ↓
fetch_and_store_3_years_data(mrkt_code="NASD")
    ↓ mrkt_code == "NASD" ?
    ↓
┌──────────────────────────────────────┐
│ Yes → foreign_api.get_stock_data()   │  ← 해외 API
│ No  → kis_api.get_stock_data()       │  ← 국내 API (기존)
└──────────────────────────────────────┘
    ↓
DB 저장 (STOCK_DAY_HISTORY)  ← 필드 매핑 동일 (foreign_api 내부 정규화)
    ↓
cache_single_indicators() → Redis
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `stock_data_batch.py` | `foreign_api.get_stock_data()` | 해외 종목 기간별 시세 데이터 조회 |
| `stock_data_batch.py` | `kis_api.get_stock_data()` | 국내 종목 기간별 시세 데이터 조회 (기존) |

---

## 3. 상세 Gap 분석

### 3.1 수정 필요 파일

| 파일 | 변경 내용 | 영향 범위 |
|------|-----------|-----------|
| `app/domain/stock/stock_data_batch.py` | API 호출 분기 추가 | `fetch_and_store_3_years_data()` |

### 3.2 이미 해결된 항목

| 항목 | 파일 | 근거 |
|------|------|------|
| 응답 필드 매핑 | `foreign_api.py:289-306` | `open→STCK_OPRC`, `high→STCK_HGPR`, `low→STCK_LWPR`, `clos→STCK_CLPR`, `tvol→ACML_VOL`, `xymd→STCK_BSOP_DATE` 변환 완료 |
| 일일 데이터 수집 (해외) | `auto_swing_batch.py:852-904` | `collect_single_stock()`에서 `_overseas` 분기로 `foreign_api.get_target_price()` 호출 + 필드 매핑 완료 |
| 스케줄러 등록 | `scheduler.py:50-85` | `us_trade_job`, `us_day_collect_job`, `us_ema_cache_warmup_job` 모두 등록 완료 |
| 배치 매매 분기 | `auto_swing_batch.py:968-990` | `us_trade_job()` → `get_active_overseas_swings()` 호출 완료 |
| 잔고 조회 분기 | `swing/service.py:155-162` | `mapping_swing()`에서 NASD → `foreign_api.get_stock_balance()` 분기 완료 |
| 주문 실행 분기 | `trading/order_executor.py` | `_overseas` 기반 `foreign_api.place_order_api()` 분기 완료 |

---

## 4. 구현 명세

### 4.1 `stock_data_batch.py` 수정

**변경 위치**: `fetch_and_store_3_years_data()` 함수 내부

#### Before (현재 코드, line 10, 55-64)

```python
# line 10
from app.external.kis_api import get_stock_data

# line 55-64 (process_date_range 내부)
async def process_date_range(range_start, range_end):
    task_start_time = time.time()
    async with semaphore:
        try:
            response = await get_stock_data(
                user_id, st_code,
                range_start.strftime('%Y%m%d'),
                range_end.strftime('%Y%m%d'),
                db
            )
```

#### After (변경 후)

```python
# line 10-11
from app.external.kis_api import get_stock_data
from app.external import foreign_api

# process_date_range 내부
async def process_date_range(range_start, range_end):
    task_start_time = time.time()
    async with semaphore:
        try:
            if mrkt_code == "NASD":
                response = await foreign_api.get_stock_data(
                    user_id, st_code,
                    range_start.strftime('%Y%m%d'),
                    range_end.strftime('%Y%m%d'),
                    db
                )
            else:
                response = await get_stock_data(
                    user_id, st_code,
                    range_start.strftime('%Y%m%d'),
                    range_end.strftime('%Y%m%d'),
                    db
                )
```

#### 동작 검증

`foreign_api.get_stock_data()` (foreign_api.py:267-308) 확인 결과:
- 시그니처: `get_stock_data(user_id, code, start_date, end_date, db)` → 국내와 동일
- 응답 구조: `{"output2": [...]}`  → 국내와 동일
- 필드 정규화: 내부에서 `open→STCK_OPRC` 등 자동 변환 → `stock_data_batch.py:96-107`의 매핑과 호환

따라서 **API 호출 분기만 추가하면 나머지 코드(DB 저장, 지표 캐싱)는 변경 없이 동작**.

---

## 5. E2E 데이터 흐름 검증

### 5.1 스윙 등록 → 매매까지 전체 흐름

```
1. POST /swing {MRKT_CODE: "NASD", ST_CODE: "AAPL", ...}
   └→ SwingService.create_swing()
   └→ SwingTrade.create(mrkt_code="NASD") — 엔티티 검증 통과 (VALID_MRKT_CODES)
   └→ DB 저장 (SWING_TRADE)

2. DATA_YN != 'Y' 시 백그라운드 태스크
   └→ _fetch_and_cache()
   └→ fetch_and_store_3_years_data(mrkt_code="NASD")
   └→ foreign_api.get_stock_data()  ← [수정 대상]
   └→ DB 저장 (STOCK_DAY_HISTORY)
   └→ cache_single_indicators() → Redis 저장

3. 스케줄러 (미국 장 시간)
   └→ us_ema_cache_warmup_job() @ 22:00 KST
   └→ us_trade_job() @ 23:00-05:25 KST (5분 간격)
       └→ get_active_overseas_swings()
       └→ process_single_swing(_overseas=True)
           └→ foreign_api.get_inquire_price() — 현재가
           └→ Strategy.check_entry_signal() — 매매 신호
           └→ foreign_api.place_order_api() — 주문
   └→ us_day_collect_job() @ 06:35 KST
       └→ collect_single_stock(_overseas=True)
       └→ foreign_api.get_target_price() — 당일 OHLCV
```

### 5.2 검증 체크리스트

- [ ] NASD 종목 스윙 등록 시 `foreign_api.get_stock_data()` 호출 확인
- [ ] 3년치 데이터가 `STOCK_DAY_HISTORY`에 올바른 필드로 저장되는지 확인
- [ ] 저장된 데이터로 `cache_single_indicators()` 정상 동작 확인
- [ ] 국내 종목(J) 스윙 등록 시 기존 `kis_api.get_stock_data()` 호출 유지 확인

---

## 6. Error Handling

기존 `fetch_and_store_3_years_data()`의 에러 처리를 그대로 활용:

| 상황 | 처리 | 코드 위치 |
|------|------|-----------|
| API 호출 실패 | `process_date_range` 내 try/except → None 반환 | line 73-75 |
| DB 저장 실패 | batch별 try/except → failed_tasks 카운트 | line 114-116 |
| 전체 실패 | `DATA_YN = 'E'` 업데이트 | line 125-130 |

추가 에러 처리 불필요 — `foreign_api.get_stock_data()`는 `kis_api.get_stock_data()`와 동일한 예외 패턴.

---

## 7. Security Considerations

- [x] 기존 인증 흐름 사용 (`_get_user_auth` → KIS OAuth 토큰)
- [x] 외부 API 호출은 기존 `fetch()` 래퍼 경유 (에러 핸들링, 로깅 포함)
- [x] 새로운 환경변수 불필요

---

## 8. Test Plan

### 8.1 검증 시나리오

| # | 시나리오 | 기대 결과 |
|---|---------|-----------|
| T-01 | NASD 종목 스윙 등록 (DATA_YN='N') | `foreign_api.get_stock_data()` 호출, STOCK_DAY_HISTORY에 데이터 적재, DATA_YN='Y' |
| T-02 | 국내(J) 종목 스윙 등록 (DATA_YN='N') | `kis_api.get_stock_data()` 호출 (기존 동작 유지) |
| T-03 | NASD 종목 데이터 적재 후 지표 캐싱 | Redis에 `indicators:{ST_CODE}` 키로 EMA20, ADX 등 저장 |
| T-04 | `us_trade_job` 배치 실행 | NASD 활성 스윙 조회 → `process_single_swing` 정상 처리 |

---

## 9. Implementation Order

| 순서 | 작업 | 파일 | 예상 변경량 |
|------|------|------|------------|
| 1 | `foreign_api` import 추가 | `stock_data_batch.py:10-11` | +1줄 |
| 2 | `process_date_range` 내 API 분기 | `stock_data_batch.py:55-64` | +6줄 (if/else) |

**총 변경량**: ~7줄 추가, 0줄 삭제

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-11 | Initial draft | 강일빈 |
