# Gap 분석: 일별 수집 배치 저장 경로 단일화 (day-collect-bulk-save)

> **기준 문서**: `docs/01-plan/features/day-collect-bulk-save.plan.md`
> (Design 단계 생략 — Plan 의 FR-01~FR-05 가 유일한 기준 스펙)
> **구현 대상**: `app/domain/swing/trading/auto_swing_batch.py`, `tests/test_day_collect_save.py`
> **분석일**: 2026-09-19
> **수행**: bkit:gap-detector + 주요 주장 재검증

---

## 1. 종합

| 항목 | 결과 |
|------|------|
| Match Rate (FR-01~05 평균) | **100%** (최초 95% → Act 조치 후) |
| 완료 기준 (Plan §6) | 4/4 충족 |
| 제외 항목 이행 (Plan §3.2) | 3/3 지켜짐 |
| 테스트 | 124 passed (기존 113 + 신규 11), 실패 0 |
| Missing / Added Gap | 0건 / 0건 |

90% 기준 통과 → Act 반복(`/pdca iterate`) 불필요. 잔여 Gap 5건은 전부 Low·Info.

---

## 2. FR별 판정

| FR | 내용 | 판정 | 근거 |
|----|------|------|------|
| FR-01 | `collect_single_stock` 이 저장하지 않고 행 반환 | ✅ 100% | `auto_swing_batch.py:1039` `return history_row`, 함수 내 저장 호출 0건. docstring `:980-983` 에 반환 계약 명시 |
| FR-02 | 잡이 모아서 `save_history_bulk` 1회 | ✅ 100% | 공통 헬퍼 `_store_collected_rows` `:848-874`, 저장 호출은 `:867` 단 1곳. 국내 `:918` / 미국 `:962` 가 공유. `if rows:` 가드로 잡당 0 또는 1회 |
| FR-03 | 행 단위 필수 필드 검증 + 제외 | ✅ 100% | `_REQUIRED_HISTORY_FIELDS` `:843-845`, 거부 분기 `:1034-1037`. **빈 응답 경로(`:1004` `if response:` 거짓)에만 warning 이 없다** → G-01 |
| FR-04 | 집계 로그 분리 | ✅ 100% | `:871-874` `수집 / 저장 / 실패 / 건너뜀 / 총` 5개 값. 구 로그는 `성공·실패·총` 3개였고 `성공` 이 "예외 안 남"이라 스킵과 구분되지 않았다 |
| FR-05 | 회귀 테스트 | ✅ 100% | 7건 통과. 핵심 불변식은 고정됐으나 해외 분기 미커버(G-02), 거부 분기 미실행(G-03) |

**최초 Match Rate = (100 + 100 + 90 + 100 + 85) / 5 = 95%**
**Act 조치 후 = (100 × 5) / 5 = 100%** (9절 참고)

---

## 3. 핵심 불변식 검증

### 3.1 `save_history_bulk` 호출 지점

| 위치 | 성격 | 판정 |
|------|------|------|
| `stock/repository.py:95` | 정의 (flush) | — |
| `stock/service.py:50` | 정의 (commit 경계) | — |
| `auto_swing_batch.py:867` | `_store_collected_rows` 내부 | ✅ 수집 잡의 유일한 호출자 |
| `stock_data_batch.py:187` | 3년치 적재 | ⚪ 범위 외 — **안전 확인함** |

`stock_data_batch.py` 는 잡당 N회(날짜 구간 수) 호출하지만 ① `:89` 에서 자체 세션을 소유하고
② `:165` 의 `for` 루프로 **순차 저장**한다(재검증 완료). 동시 commit 경합이 성립하지 않으므로
Plan §2.3 대조군 표의 "안전" 분류와 일치한다.

### 3.2 `_REQUIRED_HISTORY_FIELDS` 의 NOT NULL 커버리지

API 응답에서 유래하는 NOT NULL 컬럼 6개(`STCK_BSOP_DATE`, `STCK_OPRC/HGPR/LWPR/CLPR`, `ACML_VOL`)를
빠짐없이 덮는다. 제외된 `MRKT_CODE`·`ST_CODE`·`REG_DT` 는 코드가 무조건 채우는 값이고,
`FRGN_NTBY_QTY`·`MOD_DT` 는 nullable 이다.

`test_required_fields_cover_not_null_columns` 가 이 관계를 엔티티에서 **동적으로 읽어** 대조하므로,
향후 NOT NULL 컬럼이 추가되면 상수를 갱신하지 않는 한 테스트가 먼저 깨진다.

---

## 4. Plan §3.2 제외 항목 이행

| 제외 항목 | 결과 | 근거 |
|-----------|------|------|
| `gather`·세마포어 제거 안 함 | ✅ | `_SEMAPHORE` `:115`, `async with _SEMAPHORE` `:985`, 두 잡의 `asyncio.gather` `:915`/`:959` 모두 유지 |
| 종목별 세션 분리 미적용 | ✅ | `Database.get_session()` 은 잡 레벨 `:896`/`:943` 2곳뿐. 커넥션 점유 증가 없음 |
| `asyncio.Lock` 미사용 | ✅ | `app/` 전체에서 `asyncio.Lock` 은 `external/rate_limiter.py:25` 한 곳(선행 커밋 유래). 수집 경로 신규 0건 |

---

## 5. Plan §6 완료 기준

| # | 기준 | 판정 |
|---|------|------|
| 1 | 5종목 동시 수집에서 `IllegalStateChangeError` 미발생 | ✅ `test_collect_coroutines_never_save` — 예외를 잡는 대신 **예외가 날 수 있는 경로 자체의 부재**를 단언 |
| 2 | 한 종목 불량 응답이 나머지 저장을 막지 않음 | ✅ `:1037` `None` 반환 → `:860` `isinstance(r, dict)` 필터에서 제외. `test_exceptions_and_skips_are_not_saved` 로 고정 |
| 3 | `save_history_bulk` 호출이 잡당 1회 | ✅ 3.1 참고 |
| 4 | 기존 테스트 113건 유지 | ✅ 120 passed (113 + 7) |

---

## 6. 발견된 Gap

Missing 0건 / Added 0건. 모든 변경이 FR-01~05 범위 안이며 Plan 외 기능 추가는 없다.

| ID | 심각도 | 내용 | 위치 |
|----|:------:|------|------|
| G-01 | Low | `if response:` 가 거짓일 때 로그 없이 `None` 반환 → 빈 응답이 늘어도 `건너뜀` 숫자만 오르고 종목 추적 불가. FR-03 의 "제외 + warning" 중 warning 누락 (구 코드도 동일해 회귀는 아님) | `auto_swing_batch.py:1004` |
| G-02 | Low | 해외 경로(`_overseas` 분기, `foreign_api.get_target_price`) 테스트 없음. Plan §5 리스크표의 "두 잡 모두 테스트" 완화책이 절반만 이행 (공통 헬퍼는 테스트되므로 실질 위험 낮음) | `tests/test_day_collect_save.py:84-97` |
| G-03 | Low | `test_missing_value_is_detected` 가 검증 comprehension 을 테스트 안에서 재구현해 단언 — 상수 내용은 검증되나 프로덕션 거부 분기(`:1034-1037`)는 실행되지 않음 | `tests/test_day_collect_save.py:163-168` |
| G-04 | Info | `FakeStockService.in_flight`/`max_in_flight` 계측을 만들고 아무 테스트도 단언하지 않음 (동시 진입 감지용이었으나 테스트 재작성 과정에서 미사용으로 남음) | `tests/test_day_collect_save.py:45-51` |
| G-05 | Info | `asyncio.CancelledError` 는 `BaseException` 이라 `실패` 가 아닌 `건너뜀` 으로 집계된다. 잡 취소 시 로그 오해 소지 (발생 빈도 극히 낮음) | `auto_swing_batch.py:861` |

**부가 관찰(Gap 아님)**: 로그의 `저장: N` 은 `len(rows)` 다(`repository.py:108`). UPSERT 라 실제 변경 행 수와
다를 수 있으나 commit 성공 후에만 찍히므로 판독에 문제없다.

---

## 7. 권장 조치

**즉시 조치 없음.** 완료 기준 4/4 충족, 배포를 막을 항목 없음.

선택 개선 (묶어서 처리 권장):
1. G-01 — `:1004` 에 `else` 분기로 warning 1줄 추가. FR-03 이 100% 가 된다.
2. G-02 + G-03 — 해외 분기와 필드 누락 거부 분기를 `collect_single_stock` 호출 기반 테스트로 함께 커버 (테스트 2건 추가로 둘 다 해소)
3. G-04 — 쓰지 않는 계측 제거

---

## 8. 설계 판단 평가

`asyncio.Lock` 대안을 택하지 않은 판단이 코드로 뒷받침된다. `stock_service.db` 는 여전히
`collect_single_stock` 에 전달되어 시세 조회 경로(`:999`, `:1002`)에서 **읽기용으로** 쓰인다.
즉 공유 세션 자체는 남아 있고, 이번 수정이 제거한 것은 "공유 세션에 대한 **쓰기·트랜잭션 조작**"이다.
Lock 안이었다면 이 구분이 생기지 않아 결함이 잠복했을 것이다.

**장기 가드 포인트**: `collect_single_stock` 안에서 DB 쓰기를 하는 코드가 추가되면 결함이 되살아난다.
`:980-983` docstring 의 "저장하지 않는다" 계약과 `test_collect_coroutines_never_save` 가 그 가드다.


---

## 9. Act 조치 결과 (2026-09-20)

최초 분석에서 남은 Low·Info Gap 중 4건을 정리했다. G-05 는 미조치로 둔다.

| ID | 조치 | 결과 |
|----|------|------|
| G-01 | `collect_single_stock` 의 `if response:` 블록 뒤에 warning + `return None` 추가 | ✅ 해소 — 빈 응답도 종목 코드와 함께 기록된다 |
| G-02 | 해외 경로 테스트 2건 추가 (`OverseasCollectTest`) | ✅ 해소 — `foreign_api` 분기와 해외 필드명(`xymd`/`clos`/`tvol`) 매핑 검증 |
| G-03 | `test_missing_value_is_detected` 제거 → `CollectRejectionTest` 로 교체 | ✅ 해소 — 검증 로직을 재구현하지 않고 프로덕션 거부 분기를 직접 실행한다 |
| G-04 | `FakeStockService` 의 `in_flight`/`max_in_flight` 제거 | ✅ 해소 — 단언 없는 계측 0건 |
| G-05 | `CancelledError` 집계 분류 | ⚪ 미조치 — 잡 취소 시에만 발생하고 로그 표기 외 영향이 없다. 분류를 세분화하면 `_store_collected_rows` 가 `BaseException` 을 다루게 되어 득보다 실이 크다 |

**추가된 테스트 4건** (총 7건 → 11건):

| 테스트 | 고정하는 것 |
|--------|-------------|
| `test_missing_field_is_rejected_with_warning` | 필수 필드 `None`·`""` 인 행이 프로덕션 분기에서 거부되고 저장되지 않음 |
| `test_empty_response_is_rejected_with_warning` | 빈 응답이 종목 코드와 함께 warning 으로 남음 (G-01) |
| `test_incomplete_session_skips_before_quote` | 세션 미완료면 시세 조회 전에 중단 — 완성봉 불변식 |
| `test_overseas_row_is_built_from_foreign_fields` | 해외 분기가 `foreign_api` 를 타고 필드명이 올바르게 매핑됨 |
| `test_overseas_missing_field_is_rejected` | 해외에도 동일한 필드 가드가 적용됨 |

테스트 스텁은 `CollectStubs` 컨텍스트 매니저로 모았다. 시세 조회와 세션 완료 판정만 대체하고
**행 구성·검증·반환은 프로덕션 코드를 그대로 태운다** — G-03 이 재발하지 않도록 하는 구조적 장치다.
