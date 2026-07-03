# stock-data Design Document

> **Summary**: 장 운영 시간 중 3년치 데이터 적재 시 금일(당일) 데이터 제외 처리
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-04-15
> **Status**: Draft
> **Planning Doc**: [stock-data.plan.md](../../01-plan/features/stock-data.plan.md)

---

## 1. Overview

### 1.1 Design Goals

- `fetch_and_store_3_years_data`에서 장 운영 중일 때 금일 미확정 데이터가 적재되지 않도록 `end_date`를 조정
- 국내장(J)과 미국장(NASD) 각각의 운영 시간을 정확히 판별
- 기존 코드 최소 변경 (1개 파일, 1개 함수 추가 + `end_date` 계산 로직 수정)

### 1.2 Design Principles

- **최소 변경 원칙**: `stock_data_batch.py` 내부에서만 해결
- **기존 패턴 준수**: `scheduler.py`에서 사용 중인 `America/New_York` 타임존 패턴 재활용
- **안전한 기본값**: 판별 불가 시 전일(어제)까지만 적재 (보수적 접근)

---

## 2. Architecture

### 2.1 변경 흐름

```
스윙 등록/활성화
    │
    ▼
service.py: _fetch_and_cache()
    │
    ▼
stock_data_batch.py: fetch_and_store_3_years_data()
    │
    ▼ (변경 지점)
    ├── is_market_open(mrkt_code) 호출  ← NEW
    │     │
    │     ├── 장중 → end_date = 어제
    │     └── 장마감 → end_date = 오늘 (기존 동작)
    │
    ▼
KIS API 호출 (start_date ~ end_date)
    │
    ▼
STOCK_DAY_HISTORY 적재
```

### 2.2 기존 데이터 수집 흐름과의 관계

```
[초기 적재]                          [일일 수집]
fetch_and_store_3_years_data         day_collect_job / us_day_collect_job
  │                                    │
  ├── 3년 전 ~ end_date               ├── 당일 OHLCV 확정 데이터
  ├── 스윙 등록/활성화 시 1회          ├── 매일 장 마감 후 자동 실행
  └── end_date 조정 (이번 변경)        └── 변경 없음
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `stock_data_batch.py` | `datetime`, `zoneinfo` | 장 운영 시간 판별 |
| `fetch_and_store_3_years_data` | `is_market_open()` | end_date 결정 |

---

## 3. Detailed Design

### 3.1 `is_market_open(mrkt_code)` 함수

**위치**: `app/domain/stock/stock_data_batch.py` (모듈 내부 유틸)

```python
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

def is_market_open(mrkt_code: str) -> bool:
    """
    해당 시장이 현재 장 운영 중인지 판별한다.
    
    장중이면 True, 장 마감 후이면 False를 반환한다.
    day_collect_job 실행 시점까지를 장중으로 간주하여
    미확정 데이터 적재를 방지한다.
    
    Args:
        mrkt_code: 시장 코드 ("J"=국내, "NASD"=미국)
    
    Returns:
        True: 장 운영 중 (금일 데이터 적재 불가)
        False: 장 마감 후 (금일 데이터 적재 가능)
    """
    if mrkt_code == "NASD":
        # 미국장: America/New_York 타임존 사용 (서머타임 자동 반영)
        us_tz = ZoneInfo("America/New_York")
        now_et = datetime.now(us_tz)
        
        # 주말 체크 (토=5, 일=6)
        if now_et.weekday() >= 5:
            return False
        
        # 미국 장 운영 시간: 09:00 ~ 16:35 ET
        # (16:35까지 = us_day_collect_job 실행 시점)
        market_open = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=35, second=0, microsecond=0)
        
        return market_open <= now_et <= market_close
    else:
        # 국내장: KST (UTC+9, 서머타임 없음)
        from zoneinfo import ZoneInfo
        kr_tz = ZoneInfo("Asia/Seoul")
        now_kst = datetime.now(kr_tz)
        
        # 주말 체크
        if now_kst.weekday() >= 5:
            return False
        
        # 국내 장 운영 시간: 08:00 ~ 15:35 KST
        # (15:35까지 = day_collect_job 실행 시점)
        market_open = now_kst.replace(hour=8, minute=0, second=0, microsecond=0)
        market_close = now_kst.replace(hour=15, minute=35, second=0, microsecond=0)
        
        return market_open <= now_kst <= market_close
```

**시간 기준 근거** (`scheduler.py` 참조):

| 시장 | 장 운영 기준 | day_collect 시점 | 비고 |
|------|-------------|-----------------|------|
| 국내(J) | 08:00~15:35 KST | 15:35 KST | `CronTrigger(minute='35', hour='15')` |
| 미국(NASD) | 09:00~16:35 ET | 16:35 ET | `CronTrigger(minute='35', hour='16', timezone='America/New_York')` |

### 3.2 `fetch_and_store_3_years_data` 수정

**변경 위치**: `stock_data_batch.py:38` (`end_date` 계산 부분)

**Before:**
```python
end_date = datetime.now().date()
```

**After:**
```python
today = datetime.now().date()
if is_market_open(mrkt_code):
    # 장중이면 전일까지만 적재 (당일 미확정 데이터 제외)
    end_date = today - timedelta(days=1)
    logger.info(f"[{mrkt_code}/{st_code}] 장 운영 중 - 전일({end_date})까지 적재")
else:
    # 장 마감 후에는 당일 포함 적재
    end_date = today
    logger.info(f"[{mrkt_code}/{st_code}] 장 마감 - 금일({end_date})까지 적재")
```

### 3.3 Import 추가

```python
# 기존
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 추가
from datetime import timedelta
from zoneinfo import ZoneInfo
```

---

## 4. Edge Cases

### 4.1 경계 시간대 처리

| 시나리오 | 시간 | mrkt_code | is_market_open | end_date |
|----------|------|-----------|:--------------:|----------|
| 국내 장 시작 전 | 07:59 KST | J | `False` | 오늘 |
| 국내 장중 | 10:00 KST | J | `True` | 어제 |
| 국내 장 마감 직후 | 15:36 KST | J | `False` | 오늘 |
| 미국 장중 (서머타임) | 12:00 ET | NASD | `True` | 어제 |
| 미국 장 마감 직후 | 16:36 ET | NASD | `False` | 오늘 |
| 토요일 | 아무 시간 | J/NASD | `False` | 오늘 |
| 일요일 | 아무 시간 | J/NASD | `False` | 오늘 |

### 4.2 공휴일 처리

- 현재 scope에서는 공휴일을 별도 처리하지 않음
- 공휴일에 장이 열리지 않으므로 API에서 데이터가 반환되지 않음 → 실질적 영향 없음
- 향후 필요 시 공휴일 캘린더 연동 가능 (Out of Scope)

---

## 5. Error Handling

기존 `fetch_and_store_3_years_data`의 에러 처리 패턴을 그대로 유지한다.

| 상황 | 처리 |
|------|------|
| `is_market_open` 내부 예외 | 발생 불가 (순수 시간 비교, 외부 의존 없음) |
| `end_date`가 `start_date`보다 이전 | 발생 불가 (3년 전 ~ 어제/오늘) |
| API에서 빈 데이터 반환 | 기존 로직으로 처리 (`logger.warning`) |

---

## 6. Test Plan

### 6.1 검증 시나리오

| # | 시나리오 | 검증 방법 |
|---|---------|----------|
| 1 | 국내 장중(평일 10:00 KST) 스윙 등록 | 적재된 데이터의 최신 날짜가 전일(어제)인지 확인 |
| 2 | 국내 장마감 후(평일 16:00 KST) 스윙 등록 | 적재된 데이터의 최신 날짜가 금일(오늘)인지 확인 |
| 3 | 미국 장중(평일 12:00 ET) 스윙 등록 | 적재된 데이터의 최신 날짜가 전일인지 확인 |
| 4 | 미국 장마감 후(평일 17:00 ET) 스윙 등록 | 적재된 데이터의 최신 날짜가 금일인지 확인 |
| 5 | 주말 스윙 등록 | 적재된 데이터의 최신 날짜가 금일(금요일 데이터)인지 확인 |
| 6 | day_collect_job 정상 동작 확인 | 기존 동작 변경 없음 확인 |

---

## 7. Implementation Guide

### 7.1 변경 파일 목록

| 파일 | 변경 내용 | 변경 규모 |
|------|-----------|----------|
| `app/domain/stock/stock_data_batch.py` | `is_market_open()` 추가, `end_date` 로직 수정 | Small |

### 7.2 Implementation Order

1. [x] `zoneinfo`, `timedelta` import 추가
2. [x] `is_market_open(mrkt_code)` 함수 구현
3. [x] `fetch_and_store_3_years_data`의 `end_date` 계산 로직 수정
4. [x] 로깅 추가

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-15 | Initial draft | 강일빈 |
