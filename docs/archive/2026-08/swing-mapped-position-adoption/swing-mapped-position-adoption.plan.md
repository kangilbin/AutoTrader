# Plan: 매핑 보유종목 포지션 편입 (swing-mapped-position-adoption)

> **요약**: 계좌 보유종목으로 자동 등록된 스윙을 활성화할 때, 기존 포지션을 자동매매 관리 대상으로 정상 편입한다.
>
> **작성일**: 2026-08-26
> **상태**: Draft
> **선행 작업**: 데이터 적재 트리거 활성화 시점 단일화 (`app/domain/swing/service.py` — 완료)

---

## 1. 개요

### 1.1 목적

`mapping_swing`이 계좌 보유종목을 자동 등록할 때 만드는 `SIGNAL=0(매수대기) + HOLD_QTY>0` 상태는 내부적으로 모순이다.
이 상태의 스윙을 활성화해도 자동매매가 동작하지 않으며, 특정 조건에서는 기존 포지션 정보가 유실된다.
활성화 시 보유 포지션을 `SIGNAL=1`로 편입해 손절/익절 관리 아래 두는 것이 목적이다.

### 1.2 배경

`mapping_swing`은 `/swing/list` 호출 시 계좌 보유종목을 `SWING_TYPE='S', USE_YN='N'`으로 자동 등록한다
(`app/domain/swing/service.py:352-365`). 이때 `HOLD_QTY`와 `ENTRY_PRICE`는 실제 보유값으로 채우지만
`SIGNAL`은 기본값 `0`(매수대기), `CUR_AMOUNT`는 `Decimal(0)`으로 남는다.

---

## 2. 문제 정의

### 2.1 증상 A — 활성화해도 아무 일도 일어나지 않음

| 판정 | 결과 | 근거 |
|------|------|------|
| 매수 | 불가 | `equity = float(swing.CUR_AMOUNT)` = 0 → `target_qty=0` → "매수 수량 부족" 반환 (`auto_swing_batch.py:489-503`) |
| 손절/익절 | 미작동 | `SIGNAL=0` → `has_position()=False` → `_handle_position` 미진입 (`auto_swing_batch.py:391`) |
| PEAK 추적 | 미작동 | `update_peak_price`도 `has_position()` 가드 안에 있음 (`auto_swing_batch.py:376-377`) |

즉 100주를 보유한 종목을 활성화해도 배치가 방치한다. 중복 매수는 `CUR_AMOUNT=0`이 **우연히** 막고 있을 뿐이다.

### 2.2 증상 B — INIT_AMOUNT 증액 후 활성화 시 포지션 유실

`update_swing`의 `data["CUR_AMOUNT"] = swing.CUR_AMOUNT + diff` (`service.py:243-245`)로 투자금을 증액하면
`CUR_AMOUNT > 0`이 되어 매수가 실행되고, `transition_to_buy`가 수량/평단을 **누적이 아니라 덮어쓴다**
(`entity.py:79-80`).

재현 결과 (스텁 기반 실행):

```
전: SIGNAL=0 HOLD_QTY=100 ENTRY_PRICE=70,000
후: SIGNAL=1 HOLD_QTY=32  ENTRY_PRICE=75,000   ← 32주 신규 매수
    기대값 132주 / 평단 재계산 → 실제 32주 / 신규가로 덮어씀
```

실제 보유 132주 중 DB는 32주만 인식 → 이후 청산 시 **100주가 자동매매 밖에 방치**되고,
평단이 틀어져 손절/익절 기준선이 모두 어긋난다.

### 2.3 동일 상태의 두 번째 발생원

분할매수 진행 중에도 `SIGNAL=0 + HOLD_QTY>0`이 정상 중간 상태로 존재한다 (`auto_swing_batch.py:547`).
평시에는 `partial_exec` Redis 키가 partial 분기로 먼저 가로채므로 안전하나,
`setex`가 DB commit **이후**에 실행되므로(`auto_swing_batch.py:405-411`) Redis 쓰기 실패 / TTL(24h) 만료 /
flush 중 하나가 발생하면 DB에는 해당 상태만 남아 다음 사이클에 증상 B와 같은 덮어쓰기가 발생한다.

---

## 3. 범위

### 3.1 포함

- [ ] FR-01: 활성화 시 보유 포지션 편입 (`SIGNAL 0→1`, 수량/평단 동기화, `PEAK_PRICE` 초기화)
- [ ] FR-02: `_handle_waiting` 진입 가드 — `HOLD_QTY>0`이면 신규 진입 차단 + error 로그
- [ ] FR-03: 두 증상에 대한 재현/회귀 테스트

### 3.2 제외

- **`transition_to_buy` 누적화**: FR-01/FR-02 적용 후 도달 경로가 없다. 호출부는 `auto_swing_batch.py:544` 단 1곳이며,
  분할체결 경로는 `order_executor.py:533-538`에서 `new_hold_qty = current_hold_qty + executed_qty` +
  `calculate_avg_entry_price`로 **이미 누적/평단 재계산을 자체 수행**하고 `transition_to_buy`를 경유하지 않는다.
  정상 진입은 항상 `HOLD_QTY=0`에서 시작하므로 `= qty`와 `+= qty`의 결과가 동일 → 동작이 바뀌지 않는 변경이며
  체결 회계 경로를 무의미하게 건드리는 리스크만 남는다.
- **`day_collect_job` 수집 범위 축소**: `DATA_YN='Y'` 전체 → 활성 스윙으로 좁히는 건 별건.
  `_calculate_indicators`가 마지막 저장봉을 날짜 검증 없이 전일봉으로 쓰므로(`service.py:493-505`)
  갭 보충 로직이 선행되어야 한다.
- **`mapping_swing` merge 분기의 상시 `HOLD_QTY` 동기화**: 현재 표시용 dict만 만들고 DB를 갱신하지 않는다.
  활성화 시점 동기화(FR-01)로 필요 시점은 커버되므로 이번 범위에서 제외.

---

## 4. 요구사항

### 4.1 기능 요구사항

| ID | 요구사항 | 우선순위 | 상태 |
|----|----------|:--------:|:----:|
| FR-01 | `USE_YN 'N'→'Y'` 전환 시 `HOLD_QTY>0 && SIGNAL==0`이면 증권사 실보유 수량/평단으로 동기화하고 `SIGNAL=1`로 편입 | High | **완료** |
| FR-02 | `_handle_waiting` 진입 시 `HOLD_QTY>0`이면 매수 주문 전에 차단하고 error 로그 기록 | High | **완료** |
| FR-03 | 증상 A/B 재현 테스트를 회귀 테스트로 고정 | Medium | **완료** |
| FR-04 | 편입 시 `PEAK_PRICE = max(현재가, 평단)` 설정 — 트레일링 익절 기준 확보 | Medium | **완료** |
| FR-05 | `update_swing` 응답이 변경 전 값을 반환하는 문제 수정 (설계 단계 발견, Design 2.3) | Medium | **완료** |

### 4.2 설계 결정

| 결정 항목 | 선택 | 근거 |
|-----------|------|------|
| 편입 수량 소스 | 증권사 실보유 조회 | DB `HOLD_QTY`는 매핑 시점 스냅샷. 그 사이 수동 매도/추가매수 가능 |
| 국내 조회 API | ~~`output1` 재사용~~ → **전용 헬퍼로 재조회** | `get_available_capital`은 `/swing/available-capital`의 공개 반환 계약이라 보유목록을 끼워넣으면 오염됨 (Design 2.1에서 번복) |
| 해외 조회 API | `foreign_api.get_us_holdings` | 해외는 `get_foreign_margin`만 호출하므로 보유종목 조회 1회 추가 필요 |
| 실보유 0주인 경우 | 편입하지 않고 `HOLD_QTY=0` 정리 후 정상 매수대기 | 매핑 후 수동 전량매도한 케이스 |
| `PEAK_PRICE` 산정 | `max(현재가, 평단)` | `auto_swing_batch.py:331-335`의 기존 패턴 재사용 |
| FR-02 가드 위치 | `process_single_swing`의 `2-1` (주문 실행 **전**, `prev_signal` 캡처 앞) | 주문 후 예외를 던지면 체결과 DB가 어긋나 포지션 드리프트 발생 |

---

## 5. 변경 대상

| 파일 | 변경 내용 |
|------|-----------|
| `app/domain/swing/entity.py` | 포지션 편입 메서드 추가 (`adopt_existing_position` 등) — `SIGNAL 0→1`, 수량/평단/PEAK 설정 |
| `app/domain/swing/service.py` | `update_swing` 활성화 분기에서 실보유 조회 후 편입 호출 |
| `app/domain/swing/trading/auto_swing_batch.py` | `_handle_waiting` 진입 가드 추가 |
| `tests/` | 증상 A/B 회귀 테스트 추가 |

### 구현 순서

1. `entity.py` — 편입 메서드 + 불변식 검증 (Entity는 순수 도메인, 외부 참조 없음)
2. `auto_swing_batch.py` — FR-02 가드 (단독으로 즉시 안전망 확보)
3. `service.py` — FR-01 활성화 편입 (국내/해외 분기)
4. `tests/` — 회귀 테스트 고정
5. 런타임 검증 — 스텁 기반 시나리오 실행으로 편입 전/후 상태 확인

---

## 6. 성공 기준

- [ ] 매핑 보유종목 활성화 시 `SIGNAL=1`이 되어 `_handle_position`이 손절/익절을 평가한다
- [ ] `INIT_AMOUNT` 증액 후 활성화해도 기존 `HOLD_QTY`/`ENTRY_PRICE`가 유실되지 않는다
- [ ] `HOLD_QTY>0 && SIGNAL==0` 상태에서는 어떤 경로로도 신규 매수 주문이 나가지 않는다
- [ ] 정상 진입 경로(`HOLD_QTY=0`)의 동작은 변경 전과 동일하다 (기존 테스트 전량 통과)

---

## 7. 리스크 및 대응

| 리스크 | 영향 | 가능성 | 대응 |
|--------|:----:|:------:|------|
| 편입 직후 손절선 하회 → 즉시 전량 매도 | High | Medium | 편입은 사용자가 활성화를 누른 명시적 행위. 다만 편입 시 평단/PEAK를 정확히 세팅해 오판 방지 |
| 증권사 조회 실패로 활성화 자체가 실패 | Medium | Low | 조회 실패 시 활성화는 성공시키고 편입만 보류 + error 로그 (데이터 적재 트리거와 동일한 fail-soft 정책) |
| 해외 보유종목 조회 1회 추가로 활성화 지연 | Low | Medium | 활성화는 저빈도 사용자 액션. 기존에도 `get_foreign_margin`을 호출 중 |
| 편입 수량과 실제 체결 잔량 불일치 | High | Low | 실보유 조회값을 단일 진실로 사용 (DB 값 신뢰하지 않음) |

---

## 8. 다음 단계

1. [ ] 설계 문서 작성 (`/pdca design swing-mapped-position-adoption`)
2. [ ] 구현 (`/pdca do`)
3. [ ] Gap 분석 (`/pdca analyze`)

---

## 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| 0.1 | 2026-08-26 | 초안 — 코드 경로 추적 및 증상 A/B 런타임 재현 결과 반영 |
| 0.2 | 2026-08-26 | FR-05 추가, 구현 완료 반영, 설계 단계에서 번복된 결정 2건 정정 |
