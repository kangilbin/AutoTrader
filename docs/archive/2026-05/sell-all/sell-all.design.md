# Design: 전량 매도 API (sell-all)

## 1. 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/domain/order/schemas.py` | 수정 | `SellAllRequest` DTO 추가 |
| `app/domain/order/service.py` | 수정 | `sell_all()` 메서드 추가 |
| `app/domain/order/router.py` | 수정 | `POST /orders/sell-all` 엔드포인트 추가 |

## 2. 상세 설계

### 2.1 Schema (`app/domain/order/schemas.py`)

```python
class SellAllRequest(BaseModel):
    """전량 매도 요청"""
    ST_CODE: str    # 종목코드
    MRKT_CODE: str  # 시장구분코드 (J, NX, UN, NASD)
    QTY: int        # 매도 수량
```

### 2.2 Service (`app/domain/order/service.py`)

`sell_all()` 메서드 추가:

```python
async def sell_all(self, user_id: str, request: SellAllRequest) -> dict:
    """
    전량 매도 주문

    흐름:
    1. MRKT_CODE로 국내/해외 판별
    2. 해외: 현재가 조회 → 지정가(현재가 * 0.995) 매도
       국내: 시장가(unpr=0) 매도
    3. Order 엔티티 생성 → place_order_api 호출
    """
```

**국내/해외 분기 로직**:

```python
is_overseas = request.MRKT_CODE == "NASD"

if is_overseas:
    # 현재가 조회
    price_data = await foreign_api.get_inquire_price(user_id, request.ST_CODE, self.db)
    current_price = float(price_data.get("last", 0))
    # 지정가 = 현재가 - 0.5% (슬리피지)
    order_price = int(current_price * 0.995 * 100) / 100
    order = Order.create(
        ord_dv="sell", itm_no=request.ST_CODE, qty=request.QTY,
        unpr=order_price, excg_cd=request.MRKT_CODE
    )
    result = await foreign_api.place_order_api(user_id, order, self.db)
else:
    # 국내: 시장가 매도 (unpr=0)
    order = Order.create(
        ord_dv="sell", itm_no=request.ST_CODE, qty=request.QTY
    )
    result = await kis_api.place_order_api(user_id, order, self.db)
```

**에러 처리**:
- KIS API 응답의 `rt_cd != "0"` → `ExternalServiceError` 발생

### 2.3 Router (`app/domain/order/router.py`)

```python
@router.post("/sell-all")
async def sell_all(
    request: SellAllRequest,
    service: Annotated[OrderService, Depends(get_order_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """전량 매도 주문"""
    result = await service.sell_all(user_id, request)
    return success_response("전량 매도 주문 완료", result)
```

## 3. 시퀀스 다이어그램

```
Client → Router → Service → [MRKT_CODE 판별]
                                ├─ 국내 → Order.create(시장가) → kis_api.place_order_api()
                                └─ 해외 → foreign_api.get_inquire_price()
                                        → Order.create(지정가) → foreign_api.place_order_api()
                             ← result (주문번호 포함)
```

## 4. 구현 순서

1. `schemas.py` — `SellAllRequest` 추가
2. `service.py` — `sell_all()` 메서드 추가
3. `router.py` — `POST /orders/sell-all` 엔드포인트 추가
