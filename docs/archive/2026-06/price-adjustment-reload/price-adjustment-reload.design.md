# price-adjustment-reload Design Document

> **Summary**: 액면교체/합병/분할로 수정주가가 변경된 종목의 OHLCV를 새벽 자동 재적재
>
> **Project**: AutoTrader
> **Author**: 강일빈
> **Date**: 2026-06-17
> **Status**: Draft
> **Planning Doc**: [price-adjustment-reload.plan.md](../../01-plan/features/price-adjustment-reload.plan.md)

---

## 1. Overview

### 1.1 Design Goals

매일 새벽 02:30 KST에 한국예탁결제원(KSD) 예탁원정보 API 2종을 호출하여 당일 효력 발생 이벤트를 수집하고, 등록된 종목(`DATA_YN='Y'`) 중 영향받는 종목의 3년치 OHLCV를 KIS 수정주가 기준으로 자동 재적재한다.

### 1.2 Design Principles

- **기존 함수 최대 재사용**: `fetch_and_store_3_years_data` 그대로 사용
- **UPSERT 활용**: `save_history_bulk`가 이미 `ON DUPLICATE KEY UPDATE`이므로 DELETE 불필요
- **최소 추가 표면적**: 새 함수 3개, 스케줄러 1줄, 신규 테이블 0개
- **자연 처리 신뢰**: 상폐/신주 매핑은 다른 경로에서 자연 처리되므로 본 기능 범위 밖

---

## 2. Architecture

### 2.1 전체 흐름

```
02:30 KST (cron)
  │
  ▼
price_adjustment_reload_job()
  │
  ├─ get_rev_split_schedule(today, today)      ← KSD 액면교체일정
  ├─ get_merger_split_schedule(today, today)   ← KSD 합병/분할일정
  │
  ▼
collect_today_adjustment_events()
  │ (두 응답에서 효력일==오늘 종목코드 set 추출)
  ▼
filter_target_stocks(event_codes)
  │ (StockRepository.find_data_target_stocks(overseas=False)와 교집합)
  ▼
for each target stock:
    DATA_YN: 'Y' → 'P'
    fetch_and_store_3_years_data(...)
        │ (내부에서 KIS 기간별 OHLCV 호출 → save_history_bulk UPSERT → DATA_YN='Y' 복원)
    │ 실패 시 → DATA_YN='E'
```

### 2.2 핵심 발견: DELETE 불필요

```python
# stock/repository.py:84
async def save_history_bulk(self, history_data: List[dict]) -> int:
    query = mysql_insert(StockHistory).values(history_data)
    query = query.on_duplicate_key_update(
        STCK_OPRC=query.inserted.STCK_OPRC,
        STCK_HGPR=query.inserted.STCK_HGPR,
        STCK_LWPR=query.inserted.STCK_LWPR,
        STCK_CLPR=query.inserted.STCK_CLPR,
        ACML_VOL=query.inserted.ACML_VOL,
        MOD_DT=query.inserted.REG_DT
    )
```
→ 동일 PK(`MRKT_CODE`, `ST_CODE`, `STCK_BSOP_DATE`)면 자동 덮어쓰기. 재적재 시 별도 DELETE 단계 불필요.

---

## 3. Detailed Design

### 3.1 신규 KIS API 함수 (`app/external/kis_api.py`)

#### `get_rev_split_schedule(user_id, from_date, to_date, db)` — 액면교체일정

| 항목 | 값 |
|------|---|
| Method | GET |
| Path | `uapi/domestic-stock/v1/ksdinfo/rev-split` |
| TR-ID | `HHKDB669105C0` |
| Query Params | `SHT_CD=""` (공백), `CTS=""`, `F_DT`, `T_DT`, `MARKET_GB="0"` (전체) |

응답 `body.output1` 필드:

| 필드 | 의미 | 비고 |
|------|------|------|
| `record_date` | 기준일 (YYYYMMDD) | 변경 *전* 시점 |
| `sht_cd` | 종목코드 | 필터 키 |
| `isin_name` | 종목명 | |
| `inter_bf_face_amt` | 변경전액면가 | |
| `inter_af_face_amt` | 변경후액면가 | |
| `td_stop_dt` | 매매거래정지기간 (범위 문자열) | |
| **`list_dt`** | **상장/등록일** | **효력발생일 = KIS 수정주가 적용 시점** |

#### `get_merger_split_schedule(user_id, from_date, to_date, db)` — 합병/분할일정

| 항목 | 값 |
|------|---|
| Method | GET |
| Path | `uapi/domestic-stock/v1/ksdinfo/merger-split` |
| TR-ID | `HHKDB669104C0` |
| Query Params | `CTS=""`, `F_DT`, `T_DT`, `SHT_CD=""` (공백) |

응답 `body.output1` 필드 중 본 기능에서 사용:

| 필드 | 의미 |
|------|------|
| `sht_cd` | 종목코드 |
| `merge_type` | 합병사유 |
| `merge_rate` | 비율 |
| **`list_dt`** | **상장/등록일 = 효력발생일** |

### 3.2.1 날짜 정규화

`list_dt`는 Length 9~10 (`YYYY/MM/DD` 또는 `YYYY-MM-DD` 추정), `record_date`는 Length 8(`YYYYMMDD`). 효력일 비교 시 통일이 필요:

```python
def _normalize_date(s: str) -> str:
    """슬래시/하이픈/공백 제거 후 8자리 YYYYMMDD 반환"""
    return ''.join(c for c in (s or '') if c.isdigit())[:8]
```

### 3.2 신규 배치 모듈 (`app/domain/stock/price_adjustment_batch.py`)

```python
"""수정주가 변동 종목 자동 재적재 배치"""
from datetime import datetime
import asyncio
import logging

from app.common.database import Database
from app.domain.stock.service import StockService
from app.domain.stock.stock_data_batch import fetch_and_store_3_years_data
from app.external.kis_api import (
    get_rev_split_schedule,
    get_merger_split_schedule,
)

logger = logging.getLogger(__name__)

# 시스템 사용자 ID (배치용)
SYSTEM_USER_ID = "system"  # 또는 별도 설계 필요

# 동시 재적재 제한
_RELOAD_SEMAPHORE = asyncio.Semaphore(3)


async def price_adjustment_reload_job():
    """매일 02:30 KST 실행"""
    logger.info("[PRICE ADJ] 수정주가 재적재 잡 시작")
    today = datetime.now().strftime("%Y%m%d")

    db = await Database.get_session()
    try:
        # 1. 이벤트 수집
        event_codes = await collect_today_adjustment_events(today)
        if not event_codes:
            logger.info("[PRICE ADJ] 당일 이벤트 없음")
            return

        # 2. 등록 종목과 교집합
        stock_service = StockService(db)
        target_stocks = await stock_service.get_data_target_stocks(overseas=False)
        targets = [s for s in target_stocks if s.ST_CODE in event_codes]

        logger.info(
            f"[PRICE ADJ] 이벤트 종목 {len(event_codes)}건, "
            f"재적재 대상 {len(targets)}건"
        )
        if not targets:
            return

        # 3. 병렬 재적재 (세마포어로 제한)
        tasks = [reload_single_stock(s) for s in targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(f"[PRICE ADJ] 완료 - 성공: {success}/{len(targets)}")

    except Exception as e:
        logger.error(f"[PRICE ADJ] 잡 실패: {e}", exc_info=True)
    finally:
        await db.close()


async def collect_today_adjustment_events(user_id: str, today: str, db) -> set[str]:
    """rev-split + merger-split 응답에서 list_dt(상장/등록일)==today 종목코드 set 반환"""
    rev = await get_rev_split_schedule(user_id, today, today, db)
    mer = await get_merger_split_schedule(user_id, today, today, db)

    codes: set[str] = set()
    for row in (rev.get("output1") or []):
        if _normalize_date(row.get("list_dt", "")) == today:
            code = (row.get("sht_cd") or "").strip()
            if code:
                codes.add(code)
    for row in (mer.get("output1") or []):
        if _normalize_date(row.get("list_dt", "")) == today:
            code = (row.get("sht_cd") or "").strip()
            if code:
                codes.add(code)
    return codes


def _normalize_date(s: str) -> str:
    """YYYYMMDD, YYYY/MM/DD, YYYY-MM-DD → YYYYMMDD"""
    return ''.join(c for c in (s or '') if c.isdigit())[:8]


async def reload_single_stock(stock):
    """단일 종목 3년치 재적재 (세마포어로 동시성 제어)"""
    async with _RELOAD_SEMAPHORE:
        try:
            stock_data = {"ST_NM": stock.ST_NM}
            await fetch_and_store_3_years_data(
                user_id=SYSTEM_USER_ID,
                mrkt_code=stock.MRKT_CODE,
                st_code=stock.ST_CODE,
                stock_data=stock_data,
            )
            logger.info(f"[PRICE ADJ] 재적재 완료: {stock.ST_CODE}")
        except Exception as e:
            logger.error(f"[PRICE ADJ] 재적재 실패: {stock.ST_CODE} - {e}")
            raise
```

### 3.3 스케줄러 등록 (`app/common/scheduler.py`)

```python
# import 추가
from app.domain.stock.price_adjustment_batch import price_adjustment_reload_job

# schedule_start() 내부에 추가 (국내 스케줄 섹션 하단)
scheduler.add_job(
    price_adjustment_reload_job,
    CronTrigger(minute='30', hour='2', day_of_week='mon-fri')
)
```

### 3.4 SYSTEM_USER_ID 처리

`fetch_and_store_3_years_data(user_id, ...)`의 `user_id`는 내부에서 `_get_user_auth(user_id, db)`로만 사용된다 (`kis_api.py:112`). `_get_user_auth`는 다음 흐름:

```
1. Redis에서 f"{user_id}_access_token" 조회
2. 없으면 USER 테이블의 AUTH_KEY로 토큰 재발급
```

→ **user_id가 어떤 값이든 위 조건만 만족하면 동작**. 함수 시그니처 변경 불필요.

가장 단순한 채택:

```python
# app/domain/stock/price_adjustment_batch.py
SYSTEM_USER_ID = "mgnt"  # 기존 get_target_price 패턴 답습
# 또는
SYSTEM_USER_ID = settings.BATCH_USER_ID  # 환경변수로 명시
```

**전제 조건 (Do 단계 확인 사항)**:
- USER 테이블에 `mgnt` 또는 지정한 배치 사용자가 등록돼 있어야 함
- 해당 사용자의 AUTH_KEY가 활성 상태여야 함

→ `fetch_and_store_3_years_data` 및 `get_stock_data` 시그니처 **변경 불필요**.

---

## 4. Implementation Order

| # | 작업 | 파일 | 비고 |
|---|------|------|------|
| 1 | KSD API 응답 스키마 실측 | (수동 호출) | TR-ID, 필드명 확정 |
| 2 | `get_rev_split_schedule` 구현 | `app/external/kis_api.py` | |
| 3 | `get_merger_split_schedule` 구현 | `app/external/kis_api.py` | |
| 4 | `price_adjustment_batch.py` 신규 | `app/domain/stock/` | SYSTEM_USER_ID 상수 포함 |
| 5 | 스케줄러 등록 | `app/common/scheduler.py` | cron 1줄 |
| 6 | 로컬 검증 | (수동) | 임의 효력일 종목으로 함수 직접 호출 |

---

## 5. Edge Cases

| 케이스 | 처리 방식 |
|--------|----------|
| KSD API 응답 빈 배열 | 정상. `collect_today_adjustment_events` 빈 set 반환 → 잡 즉시 종료 |
| KSD API 호출 실패 | try/except로 잡 전체 실패 처리. 다음 날 재시도 (D-N 백필은 1단계 제외) |
| 이벤트 종목 중 미등록(`DATA_YN!='Y'`) | 교집합에서 자연 제외 |
| 같은 날 두 번 실행 | UPSERT라 결과 동일. 멱등성 확보 |
| 재적재 중 `fetch_and_store_3_years_data` 실패 | 함수 자체가 `DATA_YN='E'` 마킹. 운영자 모니터링 필요 |
| 효력일 토요일/일요일 | `day_of_week='mon-fri'`라 월요일 잡 실행. 단 KSD 응답 효력일이 영업일 기준이라 토일에 잡히지 않음 — 월요일이 새 효력일이면 그날 처리됨 |
| KIS 점검시간(보통 03:40~04:10)과 겹침 | 02:30 시작 → 종목 수에 따라 점검시간 침범 가능. 종목 N개 × 약 4초 × 세마포어3 → 200종목 약 4분 소요. 대부분 안전 |
| 한 종목이 양쪽 API에 모두 잡힘 | set으로 중복 제거되어 1번만 처리 |

---

## 6. Impact Analysis

### 6.1 변경 영향

- **trade_job**: 영향 없음 (실행 시간대 겹치지 않음)
- **day_collect_job**: 영향 없음 (15:35에 이미 종료)
- **DB 부담**: 이벤트 종목당 약 700 row UPSERT. 발생 빈도 매우 낮음 (월 수 건 수준 예상)
- **KIS API**: 1일 2회 신규 호출 + 이벤트 발생 시 종목당 약 11회(3년/100일) 호출

### 6.2 리스크

| 리스크 | 완화 |
|--------|------|
| KIS 응답 필드명 추정 오류 | Do 단계 진입 전 실제 호출로 응답 dump → 필드명 확정 |
| KSD API 페이지네이션 | 1일치라 종목 수 한정적이지만 `CTS`(연속조회키) 처리 함수에 포함 검토 |
| 새벽 KIS 토큰 만료 | 기존 `oauth_token` 캐싱/갱신 로직 그대로 사용. 재발급 자동 |
| 동시 등록(`fetch_and_store_3_years_data`) 충돌 | `DATA_YN='P'` 잠금 (기존 함수가 이미 처리) |

### 6.3 측정 지표

- 잡 실행 로그 (`[PRICE ADJ]` 프리픽스)
- 이벤트 종목 수, 재적재 성공률
- 평균 잡 실행 시간

---

## 7. 확정 사항 (Do 단계 시작 시점)

1. **TR-ID**: rev-split=`HHKDB669105C0`, merger-split=`HHKDB669104C0`
2. **응답 키**: `body.output1` (`output` 아님)
3. **종목코드 필드**: `sht_cd`
4. **효력발생일 필드**: `list_dt` (상장/등록일) — Length 9~10, 비숫자 제거 후 비교
5. **rev-split 필수 추가 파라미터**: `MARKET_GB="0"` (전체)
6. **CTS 연속조회**: API 명세상 `tr_cont` 다음조회 불가 표시 — 단일 호출로 1일치 전체 수신 가정. Do에서 응답 row 수가 비정상적으로 적으면 검토.
7. **시스템 토큰**: `settings.BATCH_USER_ID` 신규 환경변수 (실 사용자 ID 지정). `mgnt` 패턴은 `settings.API_KEY`/`SECRET_KEY`가 config/.env에 미정의된 상태라 신뢰 불가.
