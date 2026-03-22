# Plan: 주식 순위 API (홈화면용)

## 개요
클라이언트 홈화면에 주식 순위 정보를 제공하는 API를 구현한다. KIS Open API의 3가지 순위 데이터(등락률, 거래량, 체결강도)를 하나의 API로 통합 조회하여 클라이언트에 전달한다.

## 현황 분석

### KIS API 함수 (kis_api.py)

| # | 함수 | 설명 | tr_id | 응답 키 |
|---|------|------|-------|---------|
| 1 | `get_fluctuation_rank` | 등락률 순위 | FHPST01700000 | `output1` |
| 2 | `get_volume_rank` | 거래량 순위 | FHPST01710000 | `Output` |
| 3 | `get_volume_power_rank` | 체결강도 순위 | FHPST01680000 | `output` |

- 3개 함수 모두 `user_id`, `db`를 파라미터로 받아 KIS API 인증 후 호출
- 각각 리스트 형태의 순위 데이터를 반환

## 구현 범위

### 통합 순위 API (`GET /stocks/ranking`)
- `asyncio.gather`로 3개 KIS API 동시 호출
- 등락률(`fluctuation`), 거래량(`volume`), 체결강도(`volume_power`) 통합 반환

## 구현 순서

1. **Router** - `app/domain/stock/router.py`에 통합 엔드포인트 1개 추가

## 영향 범위
- `app/domain/stock/router.py` - 순위 통합 조회 엔드포인트 추가

## 기술 참고
- 순위 API는 KIS API를 직접 호출하는 패스스루(pass-through) 구조
- `asyncio.gather`로 병렬 호출하여 응답 속도 최적화
- DB 저장 불필요 (실시간 조회)
- 인증 필수 (`get_current_user` 의존성)
- 기존 stock 도메인 라우터에 추가하여 `/stocks/ranking` 경로 활용
