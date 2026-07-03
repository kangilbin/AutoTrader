# Completion Report: 전량 매도 API (sell-all)

> **Date**: 2026-05-10
> **Feature**: sell-all
> **Match Rate**: 100%
> **PDCA Iterations**: 0 (첫 구현에서 100% 달성)

## 1. 요약

사용자가 보유 종목을 전량 매도할 수 있는 REST API를 구현했다.
시장 구분 코드(`MRKT_CODE`)로 국내/해외를 판별하고, 각 시장에 맞는 `place_order_api`를 호출한다.

| 항목 | 내용 |
|------|------|
| **엔드포인트** | `POST /orders/sell-all` |
| **입력** | `ST_CODE`, `MRKT_CODE`, `QTY` |
| **국내 매도** | 시장가 (`kis_api.place_order_api`) |
| **해외 매도** | 지정가, 현재가 -0.5% (`foreign_api.place_order_api`) |

## 2. PDCA 진행 이력

| Phase | 산출물 | 상태 |
|-------|--------|------|
| Plan | `docs/01-plan/features/sell-all.plan.md` | 완료 |
| Design | `docs/02-design/features/sell-all.design.md` | 완료 |
| Do | `schemas.py`, `service.py`, `router.py` 수정 | 완료 |
| Check | `docs/03-analysis/sell-all.analysis.md` (100%) | 완료 |
| Report | 본 문서 | 완료 |

## 3. 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/domain/order/schemas.py` | `SellAllRequest` DTO 추가 |
| `app/domain/order/service.py` | `sell_all()` 메서드 추가, import 추가 |
| `app/domain/order/router.py` | `POST /orders/sell-all` 엔드포인트 추가, import 추가 |

## 4. 주요 설계 결정

| 결정 사항 | 선택 | 이유 |
|-----------|------|------|
| 매도 수량 | 클라이언트 전달 | DB 의존/API 호출 불필요, KIS가 수량 초과 자체 거부 |
| 국내 주문 방식 | 시장가 | 전량 매도 목적에 부합, 빠른 체결 |
| 해외 주문 방식 | 지정가 (현재가 -0.5%) | 미국 시장 시장가 제한, 기존 패턴 준수 |
| 구현 위치 | 기존 order 도메인 | 스윙과 독립적인 범용 매도 기능 |

## 5. Gap 분석 결과

- **Match Rate**: 100%
- **Gap**: 0건
- **개선 사항**: null 응답 방어 코드 추가 (Design 대비 방어적 개선)

## 6. 범위 외

- 체결 확인 폴링 (기존 체결 조회 API 활용)
- 스윙 상태(SIGNAL) 자동 리셋
- 분할 매도
