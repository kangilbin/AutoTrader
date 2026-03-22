# Trade History API Plan

> **Feature**: trade_history
> **Date**: 2026-03-13
> **Status**: Plan

---

## 1. 개요

매매 내역 조회 API를 구현한다. 백테스팅 응답과 동일하게 `price_history`(OHLCV)와 `ema20_history` 데이터를 함께 반환하며, 1년 단위 페이징을 지원한다.

## 2. 요구사항

### 2.1 기능 요구사항

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| FR-1 | 특정 스윙의 매매 내역 조회 | 필수 |
| FR-2 | 매매 내역과 함께 해당 종목의 price_history (OHLCV) 반환 | 필수 |
| FR-3 | 매매 내역과 함께 해당 종목의 ema20_history 반환 | 필수 |
| FR-4 | 1년 단위 페이징 (year 파라미터) | 필수 |

### 2.2 비기능 요구사항

- 인증된 사용자만 본인의 스윙 매매 내역 조회 가능 (소유권 검증)
- 기존 아키텍처 패턴(Router → Service → Repository) 준수

## 3. API 설계

### 3.1 엔드포인트

```
GET /trade-history/{swing_id}?year=2026
```

| 항목 | 값 |
|------|-----|
| Method | GET |
| Path | `/trade-history/{swing_id}` |
| Auth | JWT 필수 |
| Path Param | `swing_id` (int) - 스윙 ID |
| Query Param | `year` (int, optional) - 조회 연도. 기본값: 현재 연도 |

### 3.2 응답 구조

```json
{
  "success": true,
  "message": "매매 내역 조회 완료",
  "data": {
    "swing_id": 1,
    "st_code": "005930",
    "year": 2026,
    "trades": [
      {
        "TRADE_ID": 1,
        "TRADE_DATE": "2026-03-10T09:30:00",
        "TRADE_TYPE": "B",
        "TRADE_PRICE": 72000,
        "TRADE_QTY": 10,
        "TRADE_AMOUNT": 720000,
        "TRADE_REASONS": "[\"단일매수(1차)\", \"100% 완료\"]"
      }
    ],
    "price_history": [
      {
        "STCK_BSOP_DATE": "20260102",
        "STCK_OPRC": 71000,
        "STCK_HGPR": 73000,
        "STCK_LWPR": 70500,
        "STCK_CLPR": 72500,
        "ACML_VOL": 15000000
      }
    ],
    "ema20_history": [
      {
        "STCK_BSOP_DATE": "20260102",
        "ema20": 71250.50
      }
    ]
  }
}
```

### 3.3 페이징 방식

- `year` 파라미터로 1년 단위 조회 (예: `?year=2025` → 2025-01-01 ~ 2025-12-31)
- `year` 미지정 시 현재 연도 데이터 반환
- `trades`: 해당 연도 내 매매 내역만 필터링
- `price_history`, `ema20_history`: 해당 연도의 STOCK_DAY_HISTORY 데이터

## 4. 데이터 소스

| 데이터 | 테이블 | 조건 |
|--------|--------|------|
| 매매 내역 | TRADE_HISTORY | SWING_ID + TRADE_DATE 연도 필터 |
| 종목 코드 | SWING_TRADE | SWING_ID → ST_CODE 조회 |
| 주가 데이터 | STOCK_DAY_HISTORY | ST_CODE + STCK_BSOP_DATE 연도 필터 |
| EMA20 | STOCK_DAY_HISTORY에서 계산 | 종가 기반 EMA20 계산 (백테스팅과 동일) |

## 5. 구현 순서

```
1. Schema 정의 (Response DTO)
2. Repository 메서드 추가 (연도별 조회)
3. Service 로직 구현 (매매내역 + 주가 + EMA20 조합)
4. Router 엔드포인트 작성
```

### 5.1 파일 변경 목록

| 파일 | 작업 |
|------|------|
| `app/domain/trade_history/schemas.py` | TradeHistoryWithChartResponse 추가 |
| `app/domain/trade_history/repository.py` | find_by_swing_id_and_year() 추가 |
| `app/domain/trade_history/service.py` | get_trade_history_with_chart() 추가 |
| `app/domain/trade_history/router.py` | 신규 생성 (GET /{swing_id}) |
| `app/domain/routers.py` | trade_history router 등록 |

### 5.2 EMA20 계산 방식

백테스팅(`backtest_service.py`)과 동일한 방식:
- `STOCK_DAY_HISTORY`에서 종가(`STCK_CLPR`) 조회
- pandas EMA(span=20) 계산
- 해당 연도 범위만 슬라이싱하여 반환

## 6. 소유권 검증

스윙 조회 시 해당 스윙의 계좌가 요청 사용자 소유인지 검증 필요:
- SWING_TRADE.ACCOUNT_NO → ACCOUNT.USER_ID 확인
- 불일치 시 `PermissionDeniedError` 반환
