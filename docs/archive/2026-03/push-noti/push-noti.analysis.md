# Analysis: push-noti (푸쉬 알림)

> Design 문서: `docs/02-design/features/push-noti.design.md`
> 분석일: 2026-03-21

## Overall Match Rate: 98%

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 97% | Pass |
| Architecture Compliance | 100% | Pass |
| Convention Compliance | 100% | Pass |
| **Overall** | **98%** | **Pass** |

## 의도적 설계 변경 (Design Evolution)

| 변경 | Design | Implementation | 이유 |
|------|--------|----------------|------|
| DB 구조 | 컬럼 기반 (BUY_NOTI_YN, SELL_NOTI_YN) | 행 기반 (NOTI_TYPE + USE_YN 복합 PK) | 확장성 — 알림 유형 추가 시 DDL 변경 불필요 |
| 알림 유형 | BUY/SELL 분리 | TRADE 통합 | 사용자 요청 — 매수/매도 분리 불필요 |

## Gap 상세

### Missing Features (Design O, Implementation X): 없음

### Changed Features

| 항목 | Design | Implementation | 영향 |
|------|--------|----------------|------|
| `_build_trade_message()` | 독립 헬퍼 함수 | `send_trade_notification()` 내부 인라인 | Low |

### Added Features (Design에 없지만 구현된 개선)

| 항목 | 위치 | 설명 |
|------|------|------|
| `send_notification()` | `service.py:99-146` | 범용 푸쉬 알림 메서드 (trade가 래핑) |
| `is_enabled()` | `repository.py:43-46` | 알림 유형별 활성화 확인 편의 메서드 |
| `DatabaseError` 처리 | `service.py` 전체 | SQLAlchemyError catch + rollback |
| DEVICE_TYPE 갱신 | `service.py:67` | 토큰 재활성화 시 디바이스 타입도 갱신 |

## 파일 검증

| 파일 | 상태 |
|------|------|
| `app/domain/notification/__init__.py` | OK |
| `app/domain/notification/entity.py` | OK |
| `app/domain/notification/schemas.py` | OK |
| `app/domain/notification/repository.py` | OK |
| `app/domain/notification/service.py` | OK |
| `app/domain/notification/router.py` | OK |
| `app/external/expo_push.py` | OK |
| `app/domain/routers/__init__.py` | OK (notification_router 등록) |
| `app/main.py` | OK (notification_router include) |
| `app/common/database.py` | OK (entity import 추가) |
| `app/domain/swing/trading/auto_swing_batch.py` | OK (_fire_trade_notification 추가) |

## 권장 조치

Design 문서를 행 기반 + TRADE 통합 구조로 업데이트 (코드 변경 불필요)