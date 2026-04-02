# Plan: 해외 주식 API 지원 (foreign-stock)

## 1. 개요

### 배경
현재 AutoTrader는 국내 주식(KRX) API 호출에 한정되어 있다. 미국 주식(NASD, NYSE, AMEX) 자동매매를 지원하기 위해 KIS 해외주식 API를 통합하고, 기존 국내/해외를 자동 분기하는 구조를 만든다.

### 목표
- 기존 코드에 해외 주식 API 분기 로직 추가
- `foreign_api.py`의 미완성 함수들을 올바른 해외 주식 엔드포인트로 수정
- 배치 스케줄러에 미국 장 시간대 지원 추가
- 해외 주식 자동 스윙 매매 실행

## 2. 핵심 설계 결정

### 2-1. 국내/해외 분기 방식 — API 유형별 이원화

API 호출은 두 가지 맥락으로 나뉘며, 각각 분기 방식이 다르다:

#### A. 배치 매매 (스케줄러 자동 실행) → DB `SWING_TRADE.MRKT_CODE`

| 방식 | 장점 | 단점 | 판정 |
|------|------|------|------|
| 클라이언트 매 요청 전달 | 유연함 | 배치 자동 실행이라 전달 불가 | ❌ |
| Redis 저장 | 빠른 조회 | 이미 DB에 MRKT_CODE 있음, 이중 관리 | ❌ |
| **DB MRKT_CODE 활용** | 이미 존재하는 컬럼, 스윙 등록 시 1회 설정 | - | ✅ |

- `SWING_TRADE` 테이블에 이미 `MRKT_CODE` 컬럼 존재 (현재: J, NX, UN)
- 스윙 등록 시 클라이언트가 `MRKT_CODE`를 설정 → 배치가 자동 분기
- MRKT_CODE 값 확장: `NASD`(나스닥), `NYSE`(뉴욕), `AMEX`(아멕스) 추가

#### B. 라우터 직접 호출 (잔고 조회, 순위 API) → 클라이언트 Query Parameter

잔고 조회, 등락률/거래량/체결강도 순위 API는 종목 컨텍스트가 없어 `MRKT_CODE`로 분기할 수 없다. 클라이언트가 `market` query parameter로 시장을 지정한다.

```
GET /stocks/ranking/fluctuation?market=overseas&excg_cd=NASD
GET /stocks/ranking/fluctuation?market=domestic   (기본값)
```

**영향 받는 라우터 엔드포인트:**

| 엔드포인트 | 현재 | 변경 |
|-----------|------|------|
| `GET /stocks/price` | 국내 전용 | `market` 파라미터 추가, 해외 시 `excg_cd` 필수 |
| `GET /stocks/ranking/fluctuation` | 국내 전용 | `market` 파라미터 추가 → 국내/해외 API 분기 |
| `GET /stocks/ranking/volume` | 국내 전용 | 동일 |
| `GET /stocks/ranking/volume-power` | 국내 전용 | 동일 |
| 잔고 조회 (swing service 내부) | 국내 전용 | 스윙의 `MRKT_CODE`로 분기 (라우터 아님) |

**`market` 파라미터 설계:**
- `market`: `domestic` (기본값) | `overseas`
- `excg_cd`: `NASD` | `NYSE` | `AMEX` (market=overseas일 때 필수)
- 기존 클라이언트는 파라미터 없이 호출하면 국내로 동작 → **하위 호환성 유지**

### 2-2. API 분기 아키텍처 — 라우터 패턴

```
auto_swing_batch.py (trade_job)
    ↓ swing.MRKT_CODE 확인
    ↓
┌─────────────────────────────────────┐
│  MRKT_CODE in ('J', 'NX', 'UN')    │ → kis_api.py (국내)
│  MRKT_CODE in ('NASD','NYSE','AMEX')│ → foreign_api.py (해외)
└─────────────────────────────────────┘
```

배치 처리 흐름에서 `MRKT_CODE`를 확인하여 어떤 API 모듈을 호출할지 결정하는 분기 함수를 `process_single_swing()` 내부에 추가한다.

### 2-3. 미국 장 시간대 스케줄링

| 구분 | 한국 시간 (KST) | 비고 |
|------|-----------------|------|
| 서머타임 (3월~11월) | 22:30 ~ 05:00 | EDT (UTC-4) |
| 겨울 (11월~3월) | 23:30 ~ 06:00 | EST (UTC-5) |

- 기존 국내 스케줄(10:00-15:20 KST)과 별도로 미국 장 시간대 스케줄 추가
- 프리마켓/애프터마켓은 1차 범위에서 제외

## 3. 구현 범위

### 3-1. foreign_api.py 수정 (해외 주식 API 올바른 엔드포인트 적용)

현재 `foreign_api.py`에 여러 함수가 국내 엔드포인트를 잘못 사용하고 있어 수정 필요:

| 함수 | 현재 상태 | 수정 내용 |
|------|-----------|-----------|
| `get_stock_balance` | 국내 엔드포인트 사용 | `/uapi/overseas-stock/v1/trading/inquire-balance` + TR_ID: TTTS3012R/VTTS3012R |
| `place_order_api` | 국내 엔드포인트 사용 | `/uapi/overseas-stock/v1/trading/order` + 해외 주문 TR_ID |
| `get_inquire_price` | 국내 엔드포인트 사용 | `/uapi/overseas-price/v1/quotations/price` + TR_ID: HHDFS00000300 |
| `get_inquire_asking_price` | 국내 엔드포인트 사용 | 해외 호가 엔드포인트로 변경 |
| `check_order_execution` | 체결 확인 | 해외 체결 확인 엔드포인트 검증 |
| `get_stock_data` | 해외 엔드포인트 사용 (정상) | TR_ID 검증 필요 |

**추가 구현 함수:**
- `get_inquire_daily_ccld()` — 해외 주식 일별 체결 내역 조회
- 거래소 코드(`OVRS_EXCG_CD`) 파라미터화 (현재 "NASD" 하드코딩 제거)

### 3-2. 배치 분기 로직 (auto_swing_batch.py)

`process_single_swing()` 내부에서:

```python
# MRKT_CODE 기반 API 모듈 선택
def _is_overseas(mrkt_code: str) -> bool:
    return mrkt_code in ('NASD', 'NYSE', 'AMEX')

# 현재가 조회
if _is_overseas(swing.MRKT_CODE):
    price_data = await foreign_api.get_inquire_price(...)
else:
    price_data = await kis_api.get_inquire_price(...)

# 주문 실행
if _is_overseas(swing.MRKT_CODE):
    order_result = await foreign_api.place_order_api(...)
else:
    order_result = await kis_api.place_order_api(...)
```

**영향 받는 호출 지점:**
1. `get_inquire_price()` — 현재가 조회
2. `place_order_api()` — 주문 실행 (매수/매도)
3. `check_order_execution()` — 체결 확인
4. `get_stock_balance()` — 잔고 조회
5. `get_stock_data()` — 일별 시세 (data batch)
6. `ema_cache_warmup_job()` — 지표 캐시 워밍업

### 3-3. 라우터 분기 (stock/router.py)

종목 컨텍스트 없는 API에 `market`, `excg_cd` query parameter 추가:

```python
@router.get("/ranking/fluctuation")
async def fluctuation_rank(
    ...
    market: Annotated[str, Query(description="domestic|overseas")] = "domestic",
    excg_cd: Annotated[str | None, Query(description="NASD|NYSE|AMEX")] = None,
):
    if market == "overseas":
        response = await foreign_api.get_fluctuation_rank(user_id, db, rank_sort, excg_cd)
    else:
        response = await kis_api.get_fluctuation_rank(user_id, db, rank_sort, prc_cls)
    return success_response("등락률 순위 조회", response)
```

동일 패턴 적용 대상: `volume_rank`, `volume_power_rank`, `get_asking_price`

### 3-4. 스케줄러 확장 (scheduler.py)

```python
# 미국 장 스윙 트레이딩 (서머타임 기준 KST 22:30-05:00)
# 보수적으로 23:00-04:30 KST 설정 (장 시작/마감 30분 여유)
scheduler.add_job(
    us_trade_job,
    CronTrigger(minute='*/5', hour='23', day_of_week='mon-fri')
)
scheduler.add_job(
    us_trade_job,
    CronTrigger(minute='*/5', hour='0-4', day_of_week='tue-sat')
)
```

### 3-4. SWING_TRADE 엔티티 수정

- `MRKT_CODE` 검증에 해외 거래소 코드 추가
- 해외 주식은 소수점 가격 지원 필요 → `ENTRY_PRICE`는 이미 `DECIMAL(15,2)`로 대응 가능
- `Order` 엔티티에 `excg_cd`(거래소코드) 필드 추가 검토

### 3-5. 가격 단위 차이 처리

| 구분 | 국내 | 해외 (미국) |
|------|------|-------------|
| 가격 단위 | 정수 (원) | 소수점 (달러) |
| 수량 단위 | 정수 | 정수 (소수점 매매 미지원) |
| 통화 | KRW | USD |

- 기존 `int()` 변환 로직 → 해외는 `float()` 또는 `Decimal()` 사용으로 분기
- `SwingTrade.transition_*` 메서드의 `int` 파라미터 → `Decimal` 대응

## 4. 구현 순서

| 단계 | 작업 | 파일 |
|------|------|------|
| 1 | `Order` 엔티티에 `excg_cd` 필드 추가 | `domain/order/entity.py` |
| 2 | `SwingTrade` 엔티티 MRKT_CODE 검증 확장 | `domain/swing/entity.py` |
| 3 | `foreign_api.py` 해외 주식 API 함수 수정/구현 | `external/foreign_api.py` |
| 4 | API 분기 유틸 함수 작성 | `external/api_router.py` (신규) |
| 5 | **라우터 분기 (잔고조회, 순위 API)** | `domain/stock/router.py` |
| 6 | `auto_swing_batch.py` 분기 로직 적용 | `domain/swing/trading/auto_swing_batch.py` |
| 7 | `order_executor.py` 해외 주문 분기 | `domain/swing/trading/order_executor.py` |
| 8 | 스케줄러 미국 장 시간대 추가 | `common/scheduler.py` |
| 9 | `stock_data_batch.py` 해외 데이터 수집 분기 | `domain/stock/stock_data_batch.py` |
| 10 | 지표 캐시 워밍업 해외 지원 | `domain/swing/trading/auto_swing_batch.py` |

## 5. 범위 외 (향후)

- 프리마켓/애프터마켓 매매
- 홍콩, 일본, 중국, 베트남 등 기타 해외 거래소
- 환율 연동 손익 계산
- 서머타임 자동 감지 (1차는 수동 설정)
- 해외 주식 전용 전략 (국내와 동일 전략 공유)

## 6. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| KIS 해외 API TR_ID 불일치 | 주문 실패 | foreign_stocks.md 스펙 + KIS 공식 문서 교차 검증 |
| 미국 장 시간 서머타임 전환 | 스케줄 오작동 | 1차: 보수적 시간 범위, 향후: 자동 감지 |
| 소수점 가격 처리 오류 | 잘못된 주문가 | Decimal 일관 사용, int 변환 제거 |
| 야간 배치 서버 안정성 | 미국 장 중 서버 다운 | 기존 인프라 모니터링 활용 |
