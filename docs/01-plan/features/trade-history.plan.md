# Plan: trade-history API 완성

## 개요
프론트엔드(AutotradeMobile)에서 호출하는 trade-history API 3개 중 2개가 미구현 상태. 프론트엔드 타입 정의(`TradeStats`, `TradeHistoryPageResponse`)에 맞춰 백엔드 API를 완성한다.

## 현황 분석

### 프론트엔드 API 호출 패턴 (backEndApi.ts)

| # | 메서드 | 엔드포인트 | 파라미터 | 응답 타입 | 백엔드 상태 |
|---|--------|-----------|---------|-----------|-----------|
| 1 | GET | `/trade-history/{swingId}` | `start_date`, `end_date` (query) | `TradeHistoryWithChartResponse` | ✅ 구현됨 |
| 2 | GET | `/trade-history/{swingId}/stats` | 없음 | `TradeStats` | ❌ 미구현 |
| 3 | GET | `/trade-history/{swingId}/list` | `page` (default:1), `size` (default:100) (query) | `TradeHistoryPageResponse` | ❌ 미구현 |

### 프론트엔드 응답 타입 (types/tradeHistory.ts)

```typescript
// 매매 통계
export type TradeStats = {
    total_count: number;
    buy_count: number;
    sell_count: number;
};

// 매매 내역 페이징
export type TradeHistoryPageResponse = {
    trades: TradeHistory[];
    total_count: number;
    page: number;
    size: number;
    has_next: boolean;
};
```

## 구현 범위

### 1. 매매 통계 API (`GET /trade-history/{swingId}/stats`)
- 전체 기간 매매 건수 통계
- 총 거래 수, 매수 건수, 매도 건수 반환
- 소유권 검증 필수 (user_id → account → swing 조인)

### 2. 매매 내역 페이징 API (`GET /trade-history/{swingId}/list`)
- 페이지네이션 지원 (page, size 쿼리 파라미터)
- 기본값: page=1, size=100
- 최신순 정렬
- 소유권 검증 필수

## 구현 순서

1. **Schemas** - `TradeStatsResponse`, `TradeHistoryPageResponse` 추가
2. **Repository** - `count_by_swing_id`, `find_by_swing_id_paged` 메서드 추가
3. **Service** - `get_trade_stats`, `get_trade_history_list` 메서드 추가
4. **Router** - 2개 엔드포인트 추가

## 영향 범위
- `app/domain/trade_history/schemas.py` - 스키마 추가
- `app/domain/trade_history/repository.py` - 리포지토리 메서드 추가
- `app/domain/trade_history/service.py` - 서비스 메서드 추가
- `app/domain/trade_history/router.py` - 라우터 엔드포인트 추가

## 기술 참고
- 소유권 검증: 기존 `find_swing_with_ownership` 재사용
- 페이징: SQLAlchemy `offset/limit` + `count` 쿼리
- 응답 형식: `success_response()` 래핑 (`{ success: true, data: ... }`)
