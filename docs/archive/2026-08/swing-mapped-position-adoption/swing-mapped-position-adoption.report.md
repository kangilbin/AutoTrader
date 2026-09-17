# 매핑 보유종목 포지션 편입 완료 보고서

> **피처**: swing-mapped-position-adoption
>
> **프로젝트**: AutoTrader
>
> **작성일**: 2026-08-26
>
> **PDCA 상태**: Completed (Match Rate: 97%)
>
> **후속 변경**: 2026-09-17 — 배치 진입 가드가 DB값 대신 증권사 실보유로 편입하도록 변경.
> 본 보고서의 「3.4 알려진 제한사항」 2번째 항목이 해소됐다. 상세는
> [design 문서의 「후속 변경 상세」](swing-mapped-position-adoption.design.md#후속-변경-상세-2026-09-17) 참고.

---

## 1. 개요

### 1.1 목적

계좌 보유종목으로 자동 등록된 스윙을 활성화할 때, 기존 포지션을 자동매매 관리 대상으로 정상 편입한다.

### 1.2 문제 상황

`mapping_swing`은 `/swing/list` 호출 시 계좌 보유종목을 `SWING_TYPE='S', USE_YN='N'`으로 자동 등록하면서
`HOLD_QTY`/`ENTRY_PRICE`는 실제 보유값으로 채우고 `SIGNAL`은 기본값 `0`(매수대기), `CUR_AMOUNT`는 `0`으로 남긴다.
`SIGNAL=0 + HOLD_QTY>0`은 성립할 수 없는 상태이며, 두 가지 증상을 만들었다.

**증상 A — 활성화해도 아무 일도 일어나지 않음**

| 판정 | 결과 | 원인 |
|------|------|------|
| 매수 | 불가 | `equity = CUR_AMOUNT` = 0 → `target_qty=0` |
| 손절/익절 | 미작동 | `SIGNAL=0` → `has_position()=False` → `_handle_position` 미진입 |
| PEAK 추적 | 미작동 | 동일 가드 안에 있음 |

100주를 보유한 종목을 활성화해도 배치가 방치했다. 중복 매수는 `CUR_AMOUNT=0`이 우연히 막고 있었다.

**증상 B — 투자금 증액 후 활성화 시 포지션 유실** (런타임 재현)

```
전: SIGNAL=0 HOLD_QTY=100 ENTRY_PRICE=70,000
후: SIGNAL=1 HOLD_QTY=32  ENTRY_PRICE=75,000   ← 32주 신규 매수
    기대 132주 / 평단 재계산 → 실제 32주 / 신규가로 덮어씀
```

`transition_to_buy`가 수량/평단을 누적이 아니라 덮어쓰기 때문이다(`entity.py:79-80`).
실제 132주 중 DB는 32주만 인식 → 100주가 자동매매 밖에 방치되고 손절/익절 기준선이 어긋난다.

### 1.3 해결 방안

불변식 **`SIGNAL 0 + HOLD_QTY>0` 금지**를 두 지점에서 강제한다.

```
활성화(USE_YN N→Y)  : 증권사 실보유 조회 → SIGNAL=1 편입      ← 정상 경로
배치 사이클 진입     : 같은 상태 만나면 증권사 실보유로 편입     ← 안전망/자기치유
                      (2026-09-17 변경. 이전: DB값으로 편입)
```

편입 후 `is_waiting()`이 False가 되어 신규 매수 경로에 진입하지 않으므로,
`transition_to_buy`의 덮어쓰기가 **구조적으로 도달 불가**해진다. 체결 회계 로직은 손대지 않았다.

### 1.4 변경 파일 요약

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/domain/swing/entity.py` | 수정 (+31) | `adopt_position()`, `clear_orphan_position()` |
| `app/domain/swing/service.py` | 수정 | 적재 트리거 활성화 시점 단일화, `_fetch_broker_position()`, `_adopt_position_on_activate()`, 응답 `refresh` |
| `app/domain/swing/trading/auto_swing_batch.py` | 수정 (+67) | `2-1` 불변식 가드, 무방비 포지션 감지, 알림 3건 정정 |
| `app/domain/swing/schemas.py` | 수정 (+1) | `SwingResponse.HOLD_QTY` |
| `tests/test_swing_mapped_adoption.py` | 신규 (406줄) | 20 케이스 |
| **총 5개 파일** | | |

---

## 2. 구현 상세

### 2.1 Entity — 포지션 편입

```python
def adopt_position(self, hold_qty, entry_price, current_price) -> None:
    """기존 보유 포지션을 자동매매 관리 대상으로 편입 (SIGNAL 0 -> 1)"""
```

- 신규 매수가 아니라 **이미 보유 중인 수량의 인수**이므로 체결 이력을 남기지 않고 `CUR_AMOUNT`도 차감하지 않는다
- `PEAK_PRICE = max(현재가, 평단)` — 평단 아래로 잡히면 트레일링 익절 기준이 비정상적으로 낮아진다
- 3중 검증(상태/수량/평단)을 mutate보다 먼저 수행 → 실패 시 부분 변경이 남지 않는다
- `clear_orphan_position()` — 실보유 0주 확인 시 잔여 수량 정리(`SIGNAL=0` 유지)

### 2.2 Service — 활성화 시 편입

- `_fetch_broker_position()` — 증권사 실보유를 **단일 진실**로 사용. DB `HOLD_QTY`는 매핑 시점 스냅샷이므로 신뢰하지 않는다
  *(2026-09-17: 배치 가드가 호출자로 추가되며 `fetch_broker_position()`으로 공개 전환)*
- `foreign_api`가 해외 응답을 `pdno`/`hldg_qty`/`pchs_avg_pric`/`prpr`로 정규화하므로 국내·해외 동일 코드
- 조회 실패 / 평단 이상값은 **fail-soft** — 활성화는 성공시키고 편입만 보류(배치 가드가 다음 사이클에 처리)

### 2.3 Batch — 진입 가드

`prev_signal` 캡처 앞(`2-1`)에 배치한다. 주문 실행 **전**이라 체결과 DB가 어긋날 여지가 없고,
편입 후 아래 분기 체인이 그대로 이어져 **같은 사이클에 손절/익절이 평가**된다.

> 설계는 `4. SIGNAL별 오케스트레이션` 앞을 지정했으나, 그 위치는 `prev_signal` 캡처 뒤라서
> 편입(`0→1`)이 `_fire_trade_notification`의 매수 체결 조건을 성립시켜 **"매수 완료" 푸시를 오발신**한다.
> 이동으로 새로 실행되는 코드는 `update_peak_price` 하나이며 `PEAK ≥ 현재가`이므로 no-op이다.

### 2.4 선행 변경 — 데이터 적재 트리거 단일화

같은 세션에서 선행 처리했다. 기존에는 종목 등록(`create_swing`)과 **목록 조회**(`mapping_swing`) 시점에
3년치 주가를 적재했다. 목록 조회는 보유종목 전체를 자동 등록하는 경로여서, 활성화하지 않을 종목까지
적재되고 `DATA_YN='Y'`가 되면 `day_collect_job`이 **영구히 매일 수집**했다.

→ `_ensure_stock_data()`로 활성화 시점 단일 트리거화. `DATA_YN='P'`(진행중) 가드로 중복 적재도 차단.
응답에 `DATA_STATUS`(READY/PREPARING/SKIP)를 실어 프론트가 준비 상태를 표시할 수 있다.

---

## 3. 검증

### 3.1 테스트 결과

```
tests/test_swing_mapped_adoption.py   20 케이스
전체 스위트                            60 passed, 19 subtests
```

| 검증 항목 | 결과 |
|-----------|------|
| 고아 포지션 편입 | `SIGNAL 0→1`, `HOLD_QTY=100` 유지, 매수 0건, 체결 이력 0건 |
| 증상 B 재현 방지 | `CUR_AMOUNT>0`로 5사이클 연속 실행에도 매수 0건, 수량 유실 없음 |
| 편입 직후 평가 | 같은 사이클에서 손절 매도 1건 — 다음 틱까지 무방비 대기 없음 |
| 평단 없음 | 상태 변화 0, 주문 0건, error 로그 |
| DB 50주 ≠ 실보유 150주 | 증권사 값 150주로 편입 |
| 실보유 0주 | `SIGNAL=0` 유지, 수량 정리 |
| 조회 실패 / 평단 0 | 활성화 성공, 편입만 보류, 기존 수량·평단 훼손 없음 |
| 푸시 오발신 방지 | 편입 시 "매수 완료" 알림 0건 |
| 무방비 포지션 감지 | 지표 캐시 없음 + `HOLD_QTY>0` → error 로그 |
| 응답 신선도 | `USE_YN='Y'`, `SIGNAL=1`, `HOLD_QTY=150` 반영 |

### 3.2 Gap 분석 (97%)

독립 에이전트(gap-detector) 검증. 설계 일치 95 / 아키텍처 100 / 컨벤션 100 / 테스트 100.
설계 대비 변경 3건(가드 위치, fail-soft 추가, 전용 헬퍼 재조회)은 전부 타당 판정.

지적 2건 즉시 수정:
- **G1** 지표 캐시 없음 분기가 `has_position()`만 봐서 무방비 포지션을 warning으로 흘림 → 조건 보강
- **G5** `SwingResponse`에 `HOLD_QTY` 부재 → 필드 추가

상세: [swing-mapped-position-adoption.analysis.md](../../03-analysis/swing-mapped-position-adoption.analysis.md)

### 3.3 부수 개선 — 푸시 알림 3건 정정

Gap 분석 중 발견한 **기존 결함**(본 기능과 무관)을 함께 수정했다.

| 알림 | 문제 | 수정 |
|------|------|------|
| 전량 매도 | 조건이 `SIGNAL→0`인데 `reset_cycle`은 항상 `3`으로 전이 → **알림이 아예 발송되지 않음** | 조건 `→3`으로 정정 |
| 전량 매도 | `reset_cycle`이 수량/평단을 지운 뒤 호출되어 `0주 x 0원` | 매도 직전 수량 + 현재가 전달 |
| 1차 익절 | `transition_to_partial` 차감 후 값을 써서 **잔여 수량**을 매도 수량으로 표기, 단가는 평단 | `prev_hold_qty - hold_qty`로 매도 수량 산출, 단가는 현재가 |

### 3.4 알려진 제한사항

- 가드보다 앞선 두 분기(지표 캐시 없음 `:188`, 미확인 주문 미해결 `:372`)는 `return`하므로 편입이 지연된다.
  두 경로 모두 매수를 실행하지 않아 포지션 유실은 없다.
- ~~배치 편입은 증권사 조회 없이 DB값을 쓴다. 활성화 시 조회 실패 **AND** 사용자가 이미 전량 매도한 경우
  팬텀 `SIGNAL=1`이 생길 수 있다(두 조건 동시 성립 필요).~~
  → **해소 (2026-09-17)**: 배치 가드도 증권사 실보유를 조회한다. 실보유 0주면 편입 대신
  `clear_orphan_position`으로 정리한다. 재평가 결과 이 항목은 발생 확률은 낮으나
  **자가 회복이 불가능**한 종류였다 (매도 경로에 실보유 검증이 없어 주문이 영구 거절됨).
- `clear_orphan_position` 후 매핑 스윙은 `CUR_AMOUNT=0`이라 사용자가 `INIT_AMOUNT`를 조정해야 매수가 재개된다.
- 실서버 검증 미수행(KIS 실계좌 필요). 검증 범위는 스텁 기반 사이클 실행까지다.

---

## 4. 동작 흐름 요약

```
매핑 보유종목(100주, SIGNAL=0, CUR_AMOUNT=0) 활성화
  │
  ├─ Service: 증권사 실보유 조회
  │   ├─ 100주 @70,000 → adopt_position → SIGNAL=1, PEAK=max(현재가, 평단)
  │   ├─ 0주          → clear_orphan_position → SIGNAL=0 (정상 매수대기)
  │   └─ 조회 실패/평단 0 → 편입 보류 (활성화는 성공)
  │
  ├─ Service: 데이터 적재 여부 판단 → 미적재면 백그라운드 적재 (DATA_STATUS)
  │
  └─ Batch 사이클
      ├─ SIGNAL=1 → _handle_position → 손절/익절 정상 평가
      └─ (편입 보류된 경우) 2-1 가드가 증권사 실보유를 재조회해 편입 → 같은 사이클에 평가
          ├─ N주    → adopt_position(증권사 수량/평단) → SIGNAL=1
          ├─ 0주    → clear_orphan_position → SIGNAL=0 (정상 매수대기)
          └─ 조회 실패/평단 0 → 상태 불변, 다음 사이클 재시도
```

---

## 5. PDCA 진행 요약

```
[Plan] ✅ → [Design] ✅ → [Do] ✅ → [Check] ✅ (97%) → [Report] ✅
```

- **Plan**: 코드 경로 추적 + 증상 A/B 런타임 재현으로 문제 확정. `transition_to_buy` 누적화는 도달 경로 없음을 근거로 범위 제외
- **Design**: 불변식 2지점 강제 설계. 세션 동기화 동작 확인 중 기존 결함(FR-05) 발견해 편입
- **Do**: 5개 파일, 20 테스트 케이스. 구현 중 푸시 오발신 위험 발견해 가드 위치 조정
- **Check**: 독립 Gap 분석 97%, 지적 2건 즉시 수정
- **Report**: 본 문서
