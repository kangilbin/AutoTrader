# Trade History API Design

> **Feature**: trade_history
> **Date**: 2026-03-13
> **Plan Reference**: `docs/01-plan/features/trade_history.plan.md`
> **Status**: Design

---

## 1. API Specification

### 1.1 Endpoint

```
GET /trade-history/{swing_id}?year=2026
```

| 항목 | 값 |
|------|-----|
| Method | GET |
| Path | `/trade-history/{swing_id}` |
| Auth | JWT 필수 (`get_current_user`) |
| Path Param | `swing_id: int` |
| Query Param | `year: int` (optional, default: 현재 연도) |

### 1.2 Response Schema

```json
{
  "success": true,
  "message": "매매 내역 조회 완료",
  "data": {
    "swing_id": 1,
    "st_code": "005930",
    "year": 2026,
    "trades": [TradeHistoryResponse],
    "price_history": [PriceHistoryItem],
    "ema20_history": [Ema20HistoryItem]
  }
}
```

### 1.3 Error Cases

| 상황 | 예외 | HTTP |
|------|------|------|
| 스윙 미존재 | `NotFoundError` | 404 |
| 소유권 불일치 | `PermissionDeniedError` | 403 |
| DB 오류 | `DatabaseError` | 500 |

---

## 2. Schema Design

### 2.1 신규 Schemas (`app/domain/trade_history/schemas.py`)

```python
class PriceHistoryItem(BaseModel):
    """일별 주가 데이터"""
    STCK_BSOP_DATE: str      # 영업일자 (YYYYMMDD)
    STCK_OPRC: Decimal        # 시가
    STCK_HGPR: Decimal        # 고가
    STCK_LWPR: Decimal        # 저가
    STCK_CLPR: Decimal        # 종가
    ACML_VOL: int             # 거래량

class Ema20HistoryItem(BaseModel):
    """EMA20 데이터"""
    STCK_BSOP_DATE: str       # 영업일자
    ema20: Optional[float]    # EMA20 값 (None 가능)

class TradeHistoryWithChartResponse(BaseModel):
    """매매 내역 + 차트 데이터 응답"""
    swing_id: int
    st_code: str
    year: int
    trades: list[TradeHistoryResponse]
    price_history: list[PriceHistoryItem]
    ema20_history: list[Ema20HistoryItem]
```

---

## 3. Repository Layer

### 3.1 TradeHistoryRepository 추가 메서드

```python
async def find_by_swing_id_and_year(self, swing_id: int, year: int) -> List[TradeHistoryModel]:
    """특정 스윙의 연도별 거래 내역 조회"""
    # WHERE SWING_ID = :swing_id
    #   AND YEAR(TRADE_DATE) = :year
    # ORDER BY TRADE_DATE ASC
```

### 3.2 StockService 재사용

기존 `StockService.get_stock_history(mrkt_code, st_code, start_date)` 활용:
- `start_date`를 연도 시작일-2개월(EMA20 워밍업)로 설정
- 조회 후 pandas EMA 계산, 해당 연도만 필터링

---

## 4. Service Layer

### 4.1 TradeHistoryService 추가 메서드

```python
async def get_trade_history_with_chart(
    self, user_id: str, swing_id: int, year: int
) -> dict:
    """
    매매 내역 + 주가 차트 + EMA20 데이터 통합 조회

    Flow:
    1. SwingRepository.find_by_id(swing_id) → 스윙 존재 확인
    2. 소유권 검증: SWING → ACCOUNT_NO → ACCOUNT.USER_ID == user_id
    3. TradeHistoryRepository.find_by_swing_id_and_year(swing_id, year)
    4. StockService.get_stock_history(mrkt_code, st_code, start_date)
    5. EMA20 계산 (talib.EMA, span=20)
    6. 해당 연도 price_history, ema20_history 슬라이싱
    7. TradeHistoryWithChartResponse 조합 반환
    """
```

### 4.2 소유권 검증 상세

```python
# 1. 스윙 조회
swing = await swing_repo.find_by_id(swing_id)
if not swing:
    raise NotFoundError("스윙 전략", swing_id)

# 2. 계좌 소유권 확인 (ACCOUNT 테이블 조인)
account = await account_repo.find_by_account_no(swing.ACCOUNT_NO)
if not account or account.USER_ID != user_id:
    raise PermissionDeniedError("스윙 전략", swing_id)
```

### 4.3 EMA20 계산 로직 (백테스팅 패턴 재사용)

```python
import pandas as pd
import talib as ta

# 워밍업 기간 포함 조회 (연도 시작 2개월 전부터)
start_date = datetime(year, 1, 1) - relativedelta(months=2)
price_days = await stock_service.get_stock_history(mrkt_code, st_code, start_date)

prices_df = pd.DataFrame(price_days)
close_arr = pd.to_numeric(prices_df["STCK_CLPR"], errors="coerce").values
prices_df["ema20"] = ta.EMA(close_arr, timeperiod=20)

# 해당 연도만 필터링
year_mask = prices_df["STCK_BSOP_DATE"] >= f"{year}0101"
year_end_mask = prices_df["STCK_BSOP_DATE"] <= f"{year}1231"
year_df = prices_df.loc[year_mask & year_end_mask].copy()

price_history = year_df[["STCK_BSOP_DATE", "STCK_OPRC", "STCK_HGPR",
                          "STCK_LWPR", "STCK_CLPR", "ACML_VOL"]].to_dict(orient="records")
ema20_history = year_df[["STCK_BSOP_DATE", "ema20"]].assign(
    ema20=year_df["ema20"].round(2).where(year_df["ema20"].notna(), None)
).to_dict(orient="records")
```

---

## 5. Router Layer

### 5.1 신규 파일: `app/domain/trade_history/router.py`

```python
router = APIRouter(prefix="/trade-history", tags=["Trade History"])

@router.get("/{swing_id}")
async def get_trade_history_with_chart(
    swing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user)],
    year: int = Query(default=None, description="조회 연도 (기본: 현재 연도)")
):
    """매매 내역 + 차트 데이터 조회"""
    if year is None:
        year = datetime.now().year

    service = TradeHistoryService(db)
    result = await service.get_trade_history_with_chart(user_id, swing_id, year)
    return success_response("매매 내역 조회 완료", result)
```

### 5.2 라우터 등록: `app/domain/routers/__init__.py`

```python
from app.domain.trade_history.router import router as trade_history_router
# __all__에 "trade_history_router" 추가
```

### 5.3 메인 앱 등록: `app/main.py`

```python
from app.domain.routers import trade_history_router
app.include_router(trade_history_router)
```

---

## 6. 구현 순서

| 순서 | 파일 | 작업 |
|:----:|------|------|
| 1 | `app/domain/trade_history/schemas.py` | PriceHistoryItem, Ema20HistoryItem, TradeHistoryWithChartResponse 추가 |
| 2 | `app/domain/trade_history/repository.py` | find_by_swing_id_and_year() 추가 |
| 3 | `app/domain/trade_history/service.py` | get_trade_history_with_chart() 추가 |
| 4 | `app/domain/trade_history/router.py` | 신규 생성 |
| 5 | `app/domain/routers/__init__.py` | trade_history_router import 추가 |
| 6 | `app/main.py` | include_router 추가 |

---

## 7. 의존성 관계

```
Router (trade_history/router.py)
  └─ TradeHistoryService (trade_history/service.py)
       ├─ TradeHistoryRepository (trade_history/repository.py)
       │    └─ TradeHistoryModel (TRADE_HISTORY 테이블)
       ├─ SwingRepository (swing/repository.py)
       │    └─ SwingModel (SWING_TRADE 테이블)
       ├─ AccountRepository (account/repository.py)  ← 소유권 검증
       │    └─ AccountModel (ACCOUNT 테이블)
       └─ StockService (stock/service.py)
            └─ StockHistoryModel (STOCK_DAY_HISTORY 테이블)
```

---

## 8. 참고: 백테스팅 응답과의 차이

| 항목 | 백테스팅 | 매매 내역 API |
|------|----------|---------------|
| price_history 기간 | 최근 1년 고정 | year 파라미터로 선택 |
| EMA 계산 기준 | 3년 데이터 → 1년 슬라이싱 | 연도+2개월 → 연도 슬라이싱 |
| 매매 데이터 | 시뮬레이션 결과 | 실제 TRADE_HISTORY |
| 엔드포인트 | POST /backtesting | GET /trade-history/{swing_id} |
