# Gap Analysis: foreign-stock (해외 주식 API 지원)

> Design: `docs/02-design/features/foreign-stock.design.md`
> Date: 2026-04-01
> Match Rate: **99%**

## Overall Score

| Category | Items | Score |
|----------|:-----:|:-----:|
| 2-1 market_router.py | 5/5 | 100% |
| 2-2 Order entity.py | 2/2 | 100% |
| 2-3 SwingTrade entity.py | 2/2 | 100% |
| 2-4 foreign_api.py | 11/11 | 97% |
| 2-5 order_executor.py | 11/11 | 100% |
| 2-6 auto_swing_batch.py | 12/12 | 98% |
| 2-7 stock/router.py | 5/5 | 100% |
| 2-8 scheduler.py | 9/9 | 100% |
| 2-8r repository.py | 2/2 | 95% |
| 2-8/2-9 service.py | 3/3 | 100% |
| **Overall** | **62/62** | **99%** |

## CHANGED Items (4건, 모두 정당화됨)

| Item | Design | Implementation | 사유 |
|------|--------|---------------|------|
| `OVRS_ORD_UNPR` | `"0"` 하드코딩 | `str(order.unpr)` (슬리피지 가격) | 실제 슬리피지 적용이 더 적절 |
| `get_inquire_price` 파라미터명 | `excg_cd` | `excd` | KIS API 네이밍 규칙 일치 |
| `check_order_execution` delay | `1.0` | `2.0` | 미국 장 체결 지연 반영 |
| 해외 종가 필드 | `last` | `clos` | dailyprice 엔드포인트 실제 응답 키 |

## ADDED Items (6건, 설계 범위 확장)

| Item | 위치 | 설명 |
|------|------|------|
| `get_currency()` | market_router.py | 거래소-통화 매핑 헬퍼 |
| `modify_or_cancel_order_api()` | foreign_api.py | 해외 주문 정정/취소 |
| `get_inquire_daily_ccld_obj()` | foreign_api.py | 해외 미체결 내역 조회 |
| `get_inquire_asking_price()` | foreign_api.py | 해외 호가 조회 |
| `prdy_vrss_vol_rate = 100.0` | auto_swing_batch.py | 해외 방어적 기본값 |
| 매도 슬리피지 (-0.5%) | order_executor.py | 매수와 대칭 |

## MISSING Items: 0건

## 결론

Match Rate 99% — **PASS**. 코드 수정 불필요. 설계 문서에 4건의 변경 사항과 6건의 추가 사항을 반영하는 것을 권장.
