# Design: 해외 주식 API 지원 (foreign-stock)

> Plan 문서: `docs/01-plan/features/foreign-stock.plan.md`

## 1. 변경 대상 파일 및 영향도

| 파일 | 변경 유형 | 영향도 | 설명 |
|------|-----------|--------|------|
| `external/foreign_api.py` | **대폭 수정** | 높음 | 잘못된 엔드포인트 전면 수정, 해외 전용 API 함수 구현 |
| `external/market_router.py` | **신규** | 중간 | 국내/해외 API 분기 유틸 |
| `domain/order/entity.py` | 수정 | 낮음 | `excg_cd` 필드 추가 |
| `domain/swing/entity.py` | 수정 | 낮음 | MRKT_CODE 검증 해외 코드 추가 |
| `domain/swing/trading/order_executor.py` | 수정 | 높음 | 분기 API 호출 적용 |
| `domain/swing/trading/auto_swing_batch.py` | 수정 | 높음 | 현재가 조회, 데이터 수집 분기 |
| `domain/stock/router.py` | 수정 | 중간 | 순위/호가 API에 market 파라미터 추가 |
| `common/scheduler.py` | 수정 | 중간 | 미국 장 시간대 스케줄 추가 |

## 2. 상세 설계

### 2-1. 시장 분류 상수 및 분기 유틸 (`external/market_router.py` — 신규)

국내/해외 분기 로직을 한 곳에 집중시켜 중복 제거.

```python
"""시장 분류 및 API 분기 유틸"""

# 해외 거래소 코드
OVERSEAS_EXCHANGES = ("NASD", "NYSE", "AMEX")
# 국내 시장 코드
DOMESTIC_MARKETS = ("J", "NX", "UN")


def is_overseas(mrkt_code: str) -> bool:
    """해외 시장 여부 판단"""
    return mrkt_code in OVERSEAS_EXCHANGES
```

배치/라우터 어디서든 `is_overseas(mrkt_code)`로 분기.

### 2-2. Order 엔티티 수정 (`domain/order/entity.py`)

해외 주문 시 거래소 코드가 필요하므로 `excg_cd` 필드 추가.

```python
@dataclass
class Order:
    ord_dv: str       # buy | sell
    itm_no: str       # 종목번호
    qty: int          # 주문수량
    unpr: int = 0     # 주문단가 (시장가일 경우 0)
    excg_cd: str = "" # 해외 거래소 코드 (NASD, NYSE, AMEX) — 국내 시 빈문자열

    @classmethod
    def create(cls, ord_dv: str, itm_no: str, qty: int,
               unpr: int = 0, excg_cd: str = "") -> "Order":
        order = cls(ord_dv=ord_dv, itm_no=itm_no, qty=qty,
                    unpr=unpr, excg_cd=excg_cd)
        order.validate()
        return order
```

기존 `Order.create()` 호출부는 `excg_cd` 기본값(`""`)으로 하위호환 유지.

### 2-3. SwingTrade 엔티티 수정 (`domain/swing/entity.py`)

MRKT_CODE 검증에 해외 거래소 코드 추가.

```python
VALID_MRKT_CODES = ('J', 'NX', 'UN', 'NASD', 'NYSE', 'AMEX')

def validate(self) -> None:
    # 기존 검증...
    if self.MRKT_CODE not in VALID_MRKT_CODES:
        raise ValidationError(f"시장코드는 {VALID_MRKT_CODES} 중 하나여야 합니다")
```

### 2-4. foreign_api.py 전면 수정 (`external/foreign_api.py`)

현재 잘못 구현된 함수들을 올바른 해외 주식 KIS API 엔드포인트로 수정.

#### 2-4-1. 잔고 조회

```python
async def get_stock_balance(user_id: str, db: AsyncSession,
                            excg_cd: str = "NASD",
                            crcy_cd: str = "USD",
                            fk200="", nk200="",
                            result: Optional[List] = None):
    """해외 주식 잔고 조회"""
    user_data, access_data = await _get_user_auth(user_id, db)

    path = "uapi/overseas-stock/v1/trading/inquire-balance"
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    api_url = f"{url}/{path}"

    tr_id = "VTTS3012R" if access_data.get("simulation_yn") == "Y" else "TTTS3012R"

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "OVRS_EXCG_CD": excg_cd,
        "TR_CRCY_CD": crcy_cd,
        "CTX_AREA_FK200": fk200,
        "CTX_AREA_NK200": nk200,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    header = response["header"]
    tr_cont = header.get("tr_cont")

    if result is None:
        result = list(body.get("output1", []))
    else:
        result.extend(body.get("output1", []))

    output2 = body.get("output2", {})

    if tr_cont in ("F", "M"):
        return await get_stock_balance(
            user_id, db, excg_cd, crcy_cd,
            body.get("ctx_area_fk200", ""),
            body.get("ctx_area_nk200", ""),
            result
        )

    return {"output1": result, "output2": output2}
```

#### 2-4-2. 주문 (매수/매도)

```python
async def place_order_api(user_id: str, order: Order, db: AsyncSession):
    """해외 주식 주문"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-stock/v1/trading/order"
    api_url = f"{url}/{path}"

    sim = access_data.get("simulation_yn") == "Y"
    if order.ord_dv == "buy":
        tr_id = "VTTT1002U" if sim else "JTTT1002U"  # 미국 매수
    elif order.ord_dv == "sell":
        tr_id = "VTTT1001U" if sim else "JTTT1006U"  # 미국 매도
    else:
        return None

    headers = kis_headers(access_data, tr_id=tr_id)
    query = {
        "CANO": user_data.get("ACCOUNT_NO")[:8],
        "ACNT_PRDT_CD": user_data.get("ACCOUNT_NO")[-2:],
        "OVRS_EXCG_CD": order.excg_cd,  # Order 엔티티에서 가져옴
        "PDNO": order.itm_no,
        "ORD_QTY": str(order.qty),
        "OVRS_ORD_UNPR": "0",          # 시장가: 0
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",              # 지정가 (미국은 시장가 미지원, LOC 등 활용)
    }
    response = await fetch("POST", api_url, "KIS", body=query, headers=headers)
    body = response["body"]
    return body
```

> **주의**: 미국 주식은 시장가 주문이 제한적이다. KIS API에서 `ORD_DVSN="00"` (지정가)으로 LOC/MOC 주문을 활용하거나, 현재가 기준 약간의 슬리피지를 반영한 지정가를 사용해야 한다. 1차 구현에서는 현재가 + 0.5% 슬리피지로 지정가 매수를 설정한다.

#### 2-4-3. 현재가 조회

```python
async def get_inquire_price(user_id: str, code: str, db: AsyncSession, excg_cd: str = "NAS"):
    """해외 주식 현재가 조회"""
    user_data, access_data = await _get_user_auth(user_id, db)
    url = settings.DEV_API_URL if access_data.get("simulation_yn") == "Y" else settings.REAL_API_URL
    path = "uapi/overseas-price/v1/quotations/price"
    api_url = f"{url}/{path}"

    headers = kis_headers(access_data, tr_id="HHDFS00000300")
    query = {
        "AUTH": "",
        "EXCD": excg_cd,  # NAS, NYS, AMS 등
        "SYMB": code,
    }
    response = await fetch("GET", api_url, "KIS", params=query, headers=headers)
    body = response["body"]
    return body.get("output")
```

#### 2-4-4. 체결 확인 (미체결 조회)

기존 `get_inquire_daily_ccld_obj()`는 이미 해외 엔드포인트 사용 — 유지.

```python
async def check_order_execution(user_id: str, order_no: str, db: AsyncSession,
                                 max_retry: int = 3, delay: float = 1.0) -> Optional[dict]:
    """해외 주식 체결 확인 (폴링)"""
    import asyncio
    for attempt in range(max_retry):
        try:
            result = await get_inquire_daily_ccld_obj(user_id, db)
            if not result or "output" not in result:
                await asyncio.sleep(delay)
                continue

            for order in result.get("output", []):
                if order.get("odno") == order_no:
                    executed_qty = int(order.get("ft_ccld_qty", 0))  # 해외: ft_ccld_qty
                    if executed_qty > 0:
                        return {
                            "order_no": order_no,
                            "st_code": order.get("pdno"),
                            "avg_price": float(order.get("ft_ccld_unpr3", 0)),  # 해외: 소수점
                            "executed_qty": executed_qty,
                            "executed_amt": float(order.get("ft_ccld_amt3", 0)),
                            "trade_type": order.get("sll_buy_dvsn_cd")
                        }
                    break
            await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"[체결확인-해외] 오류: {e}")
            await asyncio.sleep(delay)
    return None
```

#### 2-4-5. 일별 시세 조회

기존 `get_stock_data()`는 이미 `uapi/overseas-price/v1/quotations/dailyprice` 사용 — TR_ID만 수정.

```python
# TR_ID 수정: FHKST03010100 (국내용) → HHDFS76240000 (해외용)
headers = kis_headers(access_data, tr_id="HHDFS76240000")
```

#### 2-4-6. 순위 API (기존 유지 + excg_cd 파라미터화)

```python
async def get_fluctuation_rank(user_id: str, db: AsyncSession,
                                rank_sort_cls_code: str = "0",
                                excg_cd: str = "NAS"):
    """해외주식 등락률 순위"""
    # 기존 로직 유지, "NASD" 하드코딩 → excg_cd 파라미터로 교체
    query = {
        ...
        "EXCD": excg_cd,  # 기존: "NAS" 하드코딩 제거
        ...
    }
```

동일 패턴을 `get_volume_rank`, `get_volume_power_rank`에도 적용.

### 2-5. order_executor.py 분기 적용

`SwingOrderExecutor`가 import하는 `place_order_api`, `check_order_execution`을 `mrkt_code`에 따라 분기.

**변경 전:**
```python
from app.external.kis_api import place_order_api, check_order_execution
```

**변경 후:**
```python
from app.external import kis_api, foreign_api
from app.external.market_router import is_overseas
```

**분기 적용 위치 — `execute_buy_with_partial`, `execute_sell_with_partial`, `continue_partial_execution`:**

각 메서드에 `mrkt_code: str = ""` 파라미터 추가.

```python
@classmethod
async def execute_buy_with_partial(
    cls,
    redis_client,
    swing_id: int,
    user_id: str,
    st_code: str,
    current_price: Decimal,
    target_amount: Decimal,
    avg_daily_amount: float,
    signal_on_complete: int,
    db=None,
    mrkt_code: str = "",    # 추가
):
    # ...
    order = Order.create(ord_dv="buy", itm_no=st_code, qty=qty,
                         excg_cd=mrkt_code if is_overseas(mrkt_code) else "")

    if is_overseas(mrkt_code):
        result = await foreign_api.place_order_api(user_id, order, db)
    else:
        result = await kis_api.place_order_api(user_id, order, db)

    # 체결 확인도 동일 분기
    if is_overseas(mrkt_code):
        execution = await _check_execution_with_retry_overseas(user_id, order_no, db)
    else:
        execution = await _check_execution_with_retry(user_id, order_no, db)
```

**가격 처리 차이:**
```python
# 국내: int
avg_price = execution.get("avg_price", int(curr_price))
# 해외: Decimal (소수점 보존)
avg_price = Decimal(str(execution.get("avg_price", curr_price)))
```

`_check_execution_with_retry` 해외용 별도 헬퍼:
```python
async def _check_execution_with_retry_overseas(
    user_id: str, order_no: str, db, max_retries: int = 2, delay: float = 2.0
):
    """해외 체결 확인 (미국 장은 체결 지연이 길 수 있음 → delay 2초)"""
    for attempt in range(max_retries):
        execution = await foreign_api.check_order_execution(user_id, order_no, db)
        if execution and execution.get("executed_qty", 0) > 0:
            return execution
        if attempt < max_retries - 1:
            await asyncio.sleep(delay)
    return None
```

### 2-6. auto_swing_batch.py 분기 적용

#### 2-6-1. process_single_swing() 현재가 조회 분기

```python
# 기존 (line 109):
current_price_data = await get_inquire_price("mgnt", st_code, swing_service.db)

# 변경:
from app.external.market_router import is_overseas
from app.external import foreign_api

mrkt_code = swing.MRKT_CODE

if is_overseas(mrkt_code):
    # 해외: MRKT_CODE → EXCD 변환 (NASD→NAS, NYSE→NYS, AMEX→AMS)
    excd = _to_excd(mrkt_code)
    current_price_data = await foreign_api.get_inquire_price("mgnt", st_code, swing_service.db, excd)
else:
    current_price_data = await get_inquire_price("mgnt", st_code, swing_service.db)
```

**MRKT_CODE → EXCD 변환 유틸** (`market_router.py`에 추가):
```python
_MRKT_TO_EXCD = {
    "NASD": "NAS",
    "NYSE": "NYS",
    "AMEX": "AMS",
}

def to_excd(mrkt_code: str) -> str:
    """SWING_TRADE.MRKT_CODE → KIS EXCD 파라미터 변환"""
    return _MRKT_TO_EXCD.get(mrkt_code, mrkt_code)
```

#### 2-6-2. 해외 현재가 응답 필드 매핑

국내와 해외의 응답 필드명이 다르므로 정규화 필요:

| 데이터 | 국내 필드 | 해외 필드 |
|--------|-----------|-----------|
| 현재가 | `stck_prpr` | `last` |
| 고가 | `stck_hgpr` | `high` |
| 저가 | `stck_lwpr` | `low` |
| 누적거래량 | `acml_vol` | `tvol` |
| 전일대비율 | `prdy_ctrt` | `rate` |

```python
if is_overseas(mrkt_code):
    current_price = Decimal(str(current_price_data.get("last", 0)))
    current_high = Decimal(str(current_price_data.get("high", current_price)))
    current_low = Decimal(str(current_price_data.get("low", current_price)))
    acml_vol = int(current_price_data.get("tvol", 0))
    frgn_ntby_qty = 0  # 해외 시 외국인 순매수 미제공
    prdy_ctrt = float(current_price_data.get("rate", 0))
else:
    # 기존 국내 로직 유지
    current_price = Decimal(str(current_price_data.get("stck_prpr", 0)))
    ...
```

#### 2-6-3. OrderExecutor 호출에 mrkt_code 전달

모든 `SwingOrderExecutor` 호출에 `mrkt_code=mrkt_code` 추가:

```python
order_result = await SwingOrderExecutor.execute_buy_with_partial(
    ...
    mrkt_code=mrkt_code,  # 추가
)
```

영향 받는 호출 위치:
- `_handle_signal_0()` — 1차 매수
- `_handle_signal_1()` — 2차 매수
- `_handle_signal_3()` — 재진입 매수
- `_execute_full_sell()` — 전량 매도
- `_execute_primary_sell()` — 1차 분할 매도
- `continue_partial_execution()` — 부분 체결 계속

#### 2-6-4. day_collect_job() 해외 데이터 수집 분기

```python
async def collect_single_stock(stock, stock_service: StockService):
    async with _SEMAPHORE:
        code = stock.ST_CODE
        mrkt_code = stock.MRKT_CODE

        if is_overseas(mrkt_code):
            excd = to_excd(mrkt_code)
            response = await foreign_api.get_target_price(code, excd)
            if response:
                history_data = [{
                    "MRKT_CODE": mrkt_code,
                    "ST_CODE": code,
                    "STCK_BSOP_DATE": datetime.now().strftime('%Y%m%d'),
                    "STCK_OPRC": response.get('open'),
                    "STCK_HGPR": response.get('high'),
                    "STCK_LWPR": response.get('low'),
                    "STCK_CLPR": response.get('last'),
                    "ACML_VOL": response.get('tvol'),
                    "FRGN_NTBY_QTY": 0,
                    "REG_DT": datetime.now()
                }]
                await stock_service.save_history_bulk(history_data)
        else:
            # 기존 국내 로직
            response = await get_target_price(code)
            ...
```

### 2-7. 라우터 분기 (`domain/stock/router.py`)

기존 엔드포인트에 `market`, `excg_cd` query parameter 추가.

```python
from app.external import kis_api, foreign_api

@router.get("/ranking/fluctuation")
async def fluctuation_rank(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)],
    rank_sort: Annotated[str, Query(description="0:상승율순, 1:하락율순")] = "0",
    prc_cls: Annotated[str, Query(description="가격분류코드")] = "1",
    market: Annotated[str, Query(description="domestic|overseas")] = "domestic",
    excg_cd: Annotated[str | None, Query(description="NASD|NYSE|AMEX (해외 시 필수)")] = None,
):
    """등락률 순위 (국내/해외)"""
    if market == "overseas":
        if not excg_cd:
            raise ValidationError("해외 시장 조회 시 excg_cd는 필수입니다")
        from app.external.market_router import to_excd
        response = await foreign_api.get_fluctuation_rank(user_id, db, rank_sort, to_excd(excg_cd))
    else:
        response = await kis_api.get_fluctuation_rank(user_id, db, rank_sort, prc_cls)
    return success_response("등락률 순위 조회", response)
```

동일 패턴 적용: `volume_rank`, `volume_power_rank`, `get_asking_price`

### 2-8. 스케줄러 확장 (`common/scheduler.py`)

```python
from app.domain.swing.trading.auto_swing_batch import (
    trade_job,
    us_trade_job,      # 신규: 해외 전용 배치
    day_collect_job,
    ema_cache_warmup_job,
    us_ema_cache_warmup_job,  # 신규: 해외 전용 워밍업
)

async def schedule_start():
    # === 기존 국내 스케줄 (변경 없음) ===
    scheduler.add_job(ema_cache_warmup_job,
        CronTrigger(minute='29', hour='8', day_of_week='mon-fri'))
    scheduler.add_job(trade_job,
        CronTrigger(minute='*/5', hour='10-14', day_of_week='mon-fri'))
    scheduler.add_job(trade_job,
        CronTrigger(minute='0,5,10,15,20', hour='15', day_of_week='mon-fri'))
    scheduler.add_job(day_collect_job,
        CronTrigger(minute='35', hour='15', day_of_week='0-4'))

    # === 미국 장 스케줄 (신규) ===
    # 서머타임 기준 KST 22:30-05:00 → 보수적 23:00-04:30
    # 겨울 기준 KST 23:30-06:00 → 보수적 00:00-05:30
    # 두 시간대를 모두 커버하는 범위: 23:00-05:30

    # 해외 지표 캐시 워밍업: 22:00 KST (장 시작 전)
    scheduler.add_job(us_ema_cache_warmup_job,
        CronTrigger(minute='0', hour='22', day_of_week='mon-fri'))

    # 해외 매매 배치: 23:00-23:55 (월~금)
    scheduler.add_job(us_trade_job,
        CronTrigger(minute='*/5', hour='23', day_of_week='mon-fri'))
    # 해외 매매 배치: 00:00-05:25 (화~토, 한국 기준 다음날)
    scheduler.add_job(us_trade_job,
        CronTrigger(minute='*/5', hour='0-5', day_of_week='tue-sat'))

    scheduler.start()
```

**`us_trade_job()` — 해외 전용 배치 함수:**

```python
async def us_trade_job():
    """미국 장 매매 신호 확인 및 실행 (5분 단위)"""
    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        redis_client = await Redis.get_connection()
        # 해외 종목만 필터링
        swing_list = await swing_service.get_active_overseas_swings()
        logger.info(f"[US BATCH START] 활성 해외 스윙 수: {len(swing_list)}")
    except Exception as e:
        logger.error(f"us_trade_job 스윙 목록 조회 실패: {e}", exc_info=True)
        return
    finally:
        await db.close()

    tasks = [process_single_swing(swing_row, redis_client) for swing_row in swing_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # ... 기존 로깅 패턴 동일
```

**SwingService에 해외 스윙 조회 메서드 추가:**

```python
async def get_active_overseas_swings(self):
    """해외 활성 스윙 목록 조회"""
    return await self.repo.find_active_by_market_type("overseas")
```

**SwingRepository에 시장 유형별 필터 추가:**

```python
from app.external.market_router import OVERSEAS_EXCHANGES

async def find_active_by_market_type(self, market_type: str):
    if market_type == "overseas":
        query = select(SwingTrade).where(
            SwingTrade.USE_YN == 'Y',
            SwingTrade.MRKT_CODE.in_(OVERSEAS_EXCHANGES)
        )
    else:
        query = select(SwingTrade).where(
            SwingTrade.USE_YN == 'Y',
            SwingTrade.MRKT_CODE.notin_(OVERSEAS_EXCHANGES)
        )
    # 기존 조인 로직 동일...
```

### 2-9. 기존 trade_job() 국내 전용 필터링

현재 `trade_job()`은 모든 활성 스윙을 처리한다. 미국 장 시간에 국내 종목을 처리하거나 그 반대가 발생하지 않도록, 기존 `trade_job()`도 국내 전용으로 필터링:

```python
async def trade_job():
    """국내 매매 신호 확인 및 실행"""
    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        redis_client = await Redis.get_connection()
        # 국내 종목만 필터링
        swing_list = await swing_service.get_active_domestic_swings()
        ...
```

## 3. 구현 순서

| 단계 | 작업 | 의존성 |
|------|------|--------|
| 1 | `external/market_router.py` 신규 작성 | 없음 |
| 2 | `domain/order/entity.py` — `excg_cd` 필드 추가 | 없음 |
| 3 | `domain/swing/entity.py` — MRKT_CODE 검증 확장 | 없음 |
| 4 | `external/foreign_api.py` — 해외 API 함수 전면 수정 | 단계 1, 2 |
| 5 | `domain/swing/trading/order_executor.py` — 분기 적용 | 단계 1, 2, 4 |
| 6 | `domain/swing/trading/auto_swing_batch.py` — 분기 적용 | 단계 1, 4, 5 |
| 7 | `domain/swing/repository.py` + `service.py` — 시장 유형별 조회 | 단계 1 |
| 8 | `domain/stock/router.py` — 라우터 market 파라미터 | 단계 4 |
| 9 | `common/scheduler.py` — 미국 장 시간대 스케줄 | 단계 6, 7 |

## 4. 응답 필드 매핑 요약

### 현재가 조회 (get_inquire_price)

| 용도 | 국내 키 | 해외 키 |
|------|---------|---------|
| 현재가 | `stck_prpr` | `last` |
| 고가 | `stck_hgpr` | `high` |
| 저가 | `stck_lwpr` | `low` |
| 시가 | `stck_oprc` | `open` |
| 누적거래량 | `acml_vol` | `tvol` |
| 전일대비율 | `prdy_ctrt` | `rate` |
| 전일대비거래량비율 | `prdy_vrss_vol_rate` | (미제공, 기본값 100) |
| 외국인순매수 | `frgn_ntby_qty` | (미제공, 기본값 0) |

### 체결 확인 (check_order_execution)

| 용도 | 국내 키 | 해외 키 |
|------|---------|---------|
| 체결수량 | `tot_ccld_qty` | `ft_ccld_qty` |
| 체결단가 | `avg_prvs` | `ft_ccld_unpr3` |
| 체결금액 | `tot_ccld_amt` | `ft_ccld_amt3` |

### 일별 시세 (day_collect_job)

| 용도 | 국내 키 | 해외 키 |
|------|---------|---------|
| 시가 | `stck_oprc` | `open` |
| 고가 | `stck_hgpr` | `high` |
| 저가 | `stck_lwpr` | `low` |
| 종가 | `stck_clpr` | `last` (또는 `clos`) |
| 거래량 | `acml_vol` | `tvol` |

## 5. 제약사항 및 주의사항

1. **미국 시장가 주문 제한**: KIS API에서 미국 주식 시장가 주문이 제한적 → 현재가 + 슬리피지 지정가로 대체
2. **소수점 가격**: 해외는 `float`/`Decimal` 처리, 기존 `int()` 변환 로직 수정 필요
3. **서머타임**: 1차 구현에서는 두 시간대를 모두 커버하는 넓은 범위 설정
4. **해외 외국인 순매수**: 미제공 → 전략의 `frgn_ntby_qty` 의존 부분에 fallback 처리
5. **거래소 코드 차이**: SWING_TRADE.MRKT_CODE는 `NASD`이지만, KIS 시세 API EXCD 파라미터는 `NAS` → `to_excd()` 변환 필요
