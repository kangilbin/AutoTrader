# Plan: 전량 매도 API (sell-all)

## 1. 개요

사용자가 보유한 특정 종목의 전량을 한 번에 매도하는 API를 제공한다.
시장 구분 코드(`MRKT_CODE`)를 기반으로 국내/해외 주식을 판별하고, 해당 시장의 `place_order_api`를 호출하여 전량 매도를 실행한다.

## 2. 배경 및 필요성

- 현재 스윙 매매에서는 자동 매도(분할/익절)만 지원하며, 사용자가 수동으로 특정 종목을 전량 매도하는 API가 없음
- 긴급 매도(손절, 시장 급변 대응) 시 빠르게 전량 매도할 수 있는 엔드포인트 필요

## 3. 기능 요구사항

### FR-01: 전량 매도 주문 실행
- **입력**: 종목코드(`ST_CODE`), 시장구분코드(`MRKT_CODE`), 매도수량(`QTY`)
- **매도 수량**: 클라이언트가 잔고 화면에서 확인한 수량을 직접 전달 (수량 초과 시 KIS API가 자체 거부)
- **처리**:
  1. `MRKT_CODE`로 국내/해외 판별 (`NASD` → 해외, 그 외 → 국내)
  2. 해외 주식: 현재가 조회 후 `foreign_api.place_order_api()` 호출 (지정가 주문, 현재가 -0.5% 슬리피지)
  3. 국내 주식: `kis_api.place_order_api()` 호출 (시장가 주문)
  4. 주문 결과 반환

### FR-02: 해외 주식 현재가 조회
- 해외 주식 매도 시 지정가 주문이 필요하므로 현재가를 먼저 조회
- `foreign_api.get_inquire_price()` 사용

## 4. 기술 설계 방향

### 4.1 엔드포인트

```
POST /orders/sell-all
```

### 4.2 요청/응답

**Request Body**:
```json
{
  "ST_CODE": "005930",
  "MRKT_CODE": "J",
  "QTY": 10
}
```

**Response**:
```json
{
  "success": true,
  "message": "전량 매도 주문 완료",
  "data": {
    "rt_cd": "0",
    "msg1": "...",
    "output": { "ODNO": "주문번호", ... }
  }
}
```

### 4.3 구현 위치

기존 `order` 도메인에 추가:

| 파일 | 변경 내용 |
|------|-----------|
| `app/domain/order/schemas.py` | `SellAllRequest` DTO 추가 |
| `app/domain/order/service.py` | `sell_all()` 메서드 추가 |
| `app/domain/order/router.py` | `POST /orders/sell-all` 엔드포인트 추가 |

### 4.4 시장 구분 로직

```python
# MRKT_CODE 기반 판별
if mrkt_code == "NASD":
    # 해외(미국) → foreign_api.place_order_api()
else:
    # 국내(J, NX, UN) → kis_api.place_order_api()
```

### 4.5 기존 코드 활용

- `Order.create(ord_dv="sell", itm_no=st_code, qty=qty, ...)` — 주문 엔티티 생성
- `kis_api.place_order_api()` — 국내 시장가 매도
- `foreign_api.place_order_api()` — 해외 지정가 매도
- `foreign_api.get_inquire_price()` — 해외 현재가 조회 (지정가 산출용)

## 5. 구현 순서

1. `schemas.py` — `SellAllRequest` DTO 추가
2. `service.py` — `sell_all()` 비즈니스 로직 추가
3. `router.py` — `POST /orders/sell-all` 엔드포인트 추가

## 6. 범위 외 (Out of Scope)

- 체결 확인 폴링 (주문 접수만 처리, 체결 확인은 기존 체결 조회 API 활용)
- 스윙 상태(SIGNAL) 자동 리셋 (스윙 매매와 독립적인 수동 매도)
- 분할 매도 (전량을 단일 주문으로 처리)
