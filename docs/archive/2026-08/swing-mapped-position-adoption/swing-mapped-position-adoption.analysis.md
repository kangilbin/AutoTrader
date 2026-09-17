# Gap 분석: 매핑 보유종목 포지션 편입

> Plan: [swing-mapped-position-adoption.plan.md](../01-plan/features/swing-mapped-position-adoption.plan.md)
> Design: [swing-mapped-position-adoption.design.md](../02-design/features/swing-mapped-position-adoption.design.md)
> 분석일: 2026-08-26 | 검증자: gap-detector (독립 에이전트) + 자체 재확인

## Match Rate: 97%

| 축 | 점수 |
|----|-----:|
| 설계 일치도 | 95% |
| 아키텍처 준수 | 100% |
| 컨벤션 준수 | 100% |
| 테스트 커버리지 | 100% |

## 요구사항 판정

| ID | 요구사항 | 판정 | 구현 위치 |
|----|----------|:----:|-----------|
| FR-01 | 활성화 시 실보유 기준 편입 | 100% | `service.py:194-259`, 연결 `service.py:318` |
| FR-02 | 신규 매수 차단 (불변식 가드) | 100% | `auto_swing_batch.py:375-396` |
| FR-03 | 회귀 테스트 | 100% | `tests/test_swing_mapped_adoption.py` 18케이스 |
| FR-04 | `PEAK = max(현재가, 평단)` | 100% | `entity.py:104` |
| FR-05 | 응답 신선도 + 편입 결과 반영 | 100% | `service.py:324` `db.refresh` + `schemas.py` `HOLD_QTY` 추가 |

설계 테스트 계획 8케이스 → 8/8 매핑. 설계에 없는데 추가된 테스트 10건(엔티티 단위 5, 푸시 오발신 방지 1, 평단 0 fail-soft 1, 캐시 없음 무방비 감지 1, 기타 2).

## 설계 대비 변경 (전부 타당 판정)

| 변경 | 사유 |
|------|------|
| 가드 위치 `4-0` → `2-1` | 설계 위치는 `prev_signal` 캡처 뒤라서 편입이 "매수 완료" 푸시로 오발신됨. 이동으로 새로 실행되는 코드는 `update_peak_price` 하나이며 `PEAK >= 현재가`이므로 no-op — 결과 동일 |
| `try/except ValidationError` 추가 | 증권사 평단 0 반환 시 422 응답 + 실제로는 활성화됨 불일치 방지 |
| 국내 조회를 전용 헬퍼로 재조회 | `get_available_capital`의 공개 반환 계약 오염 회피 |

## Gap 목록 및 처리

| # | 심각도 | 내용 | 처리 |
|---|:------:|------|------|
| G1 | Medium | 지표 캐시 없음 분기(`:188`)가 `has_position()`만 봐서 `SIGNAL=0 + HOLD_QTY>0` 무방비 포지션을 warning으로 흘림 | **수정 완료** — 조건에 `(swing.HOLD_QTY or 0) > 0` 추가, 테스트 고정. 이 분기는 `return`하므로 매수는 없었고 관측 누락이 실제 영향이었음 |
| G5 | Low | `SwingResponse`에 `HOLD_QTY` 부재로 편입 수량을 클라이언트가 확인 불가 | **수정 완료** — `schemas.py`에 `HOLD_QTY` 추가, 응답 검증 테스트 추가. `PEAK_PRICE`는 내부 추적값이라 미노출 |
| G2 | Low-Med | 미확인 주문 미해결 시(`:372`) 가드 미도달 → 최대 `MAX_PENDING_ATTEMPTS` 사이클 편입 지연 | **문서화** — 미체결 주문이 수량을 바꿀 수 있으므로 편입을 미루는 것이 안전. 설계 "알려진 제약"에 기재 |
| G3 | ~~Low~~ → **Med** | 배치 가드는 DB값을 신뢰 → 활성화 시 조회 실패 AND 사용자 전량매도 동시 성립 시 팬텀 `SIGNAL=1` | ~~**문서화** — 두 조건 동시 성립 필요로 확률 낮음. 설계 "알려진 제약"에 기재~~<br>**수정 완료 (2026-09-17)** — 가드가 증권사 실보유를 조회하도록 변경, 괴리 3경로 테스트 추가. **심각도 재평가**: 발생 확률은 낮지만 매도 경로에 실보유 검증이 없어(`order_executor.execute_sell_with_partial`) 주문이 영구 거절되고 재동기화 코드가 없다 — 자가 회복 불가라 Low가 아니었다. 또한 `mapping_swing`이 스냅샷을 갱신하지 않아 괴리 폭이 당초 가정보다 크다 |
| G4 | Low | `clear_orphan_position` 후 `CUR_AMOUNT=0`이라 영구 매수 불가 (사용자 가시 신호 없음) | **기존 설계 제약** — 사용자가 `INIT_AMOUNT` 조정으로 배정. 이미 문서화됨 |
| G6 | Info | service commit 직후 배치가 먼저 편입하는 경쟁 조건 | 손상 없음 — service 경로는 증권사 값으로 재수렴 |
| G7 | Info | 거울 불변식 `SIGNAL 1/2 + HOLD_QTY=0` 미강제 | 범위 외 |
| G8 | Info | `_fire_trade_notification`의 전량매도 분기 미도달 | **범위 외 별건 (아래)** |

## 범위 외 발견 — 전량매도 푸시 알림 미발송

`_fire_trade_notification`의 전량매도 분기는 `new_signal == 0 and prev_signal in (1, 2)`
(`auto_swing_batch.py:946`)이지만, `reset_cycle`은 **항상 `SIGNAL=3`**을 세팅한다(`entity.py:130`).
전량매도 경로는 `:304`와 `:751` 두 곳 모두 `reset_cycle`을 쓰므로 `new_signal`이 0이 되는 일이 없다.
→ **전량매도/2차 익절 완료 푸시가 발송되지 않는다.** dead code가 아니라 알림 누락이다.

본 기능과 무관한 기존 결함이므로 이번 변경에 포함하지 않았다. 별도 처리 필요.

## 결론

Match Rate 97% ≥ 90% → `/pdca iterate` 불필요. G1/G5 즉시 수정 완료 후 전체 스위트 **57 passed, 19 subtests**.
남은 Gap은 모두 Low/Info이며 확률이 낮거나 범위 외로, 설계 문서의 "알려진 제약"에 반영했다.
