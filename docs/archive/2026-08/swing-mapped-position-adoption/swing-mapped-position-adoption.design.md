# Design: 매핑 보유종목 포지션 편입

> Plan 문서: [swing-mapped-position-adoption.plan.md](../../01-plan/features/swing-mapped-position-adoption.plan.md)

> ### 📌 후속 변경 (2026-09-17)
>
> **배치 진입 가드가 DB값이 아니라 증권사 실보유를 기준으로 편입하도록 변경되었다.**
> 아래 본문 중 "배치는 DB값으로 편입한다"는 서술은 이 날짜 이전 구현을 기준으로 한 것이며,
> 해당 지점은 `### 3. Batch` 절과 "알려진 제약" 표에 정정 표기를 달았다.
>
> 이 문서의 「알려진 제약」에 Low로 기재했던 **"배치 가드의 DB값 신뢰"**(= analysis G3)가
> 변경 사유다. 아카이브 시점 평가는 "활성화 시 조회 실패 **AND** 사용자 전량매도 동시 성립,
> 확률 낮음"이었으나, 재검토에서 두 가지가 추가로 확인됐다.
>
> 1. `mapping_swing`의 merge 분기는 기존 행의 `HOLD_QTY`/`ENTRY_PRICE`를 **영구히 갱신하지 않는다.**
>    최초 등록 시점 스냅샷이 활성화까지 몇 달 묵을 수 있어, 괴리 폭이 「알려진 제약」의
>    또 다른 항목이 가정한 것보다 크다.
> 2. 매도 경로(`order_executor.execute_sell_with_partial`)에 실보유 검증이 없어, 수량이 과다 계상되면
>    주문이 매 사이클 거절되고 **자가 회복되지 않는다.** 재동기화 코드가 없어 수동 개입이 필요하다.
>
> 대안으로 "편입 실패 시 `USE_YN`을 'N'으로 롤백"도 검토했으나, 이 문서가 명시한 설계 원칙
> (편입 실패가 활성화를 막지 않는다 / 배치가 최종 백스톱)을 뒤집는 방향이라 채택하지 않았다.
> 대신 백스톱 자체를 정확하게 만드는 쪽을 택했다. 변경 상세는 문서 말미 「후속 변경 상세」 참고.

## 변경 요약

`SIGNAL=0(매수대기) + HOLD_QTY>0`은 성립할 수 없는 상태다. 이 불변식을 두 지점에서 강제한다.

```
활성화(USE_YN N→Y)  : 증권사 실보유를 조회해 SIGNAL=1로 편입      ← 정상 경로
배치 사이클 진입     : 같은 상태를 만나면 증권사 실보유로 편입      ← 안전망/자기치유
                      (2026-09-17 변경. 이전: DB값으로 편입)
```

편입 후에는 `has_position()=True`가 되어 `_handle_position`이 손절/익절을 평가하고,
`is_waiting()`이 False이므로 신규 매수 경로에 진입하지 않아 `transition_to_buy`의 덮어쓰기가 구조적으로 차단된다.

---

## 상태 불변식

| 상태 | HOLD_QTY | ENTRY_PRICE | 의미 |
|------|:--------:|:-----------:|------|
| SIGNAL 0 | **0** | None | 매수 대기 (신규 진입 가능) |
| SIGNAL 1/2 | > 0 | 필수 | 포지션 보유 (손절/익절 평가 대상) |
| SIGNAL 3/4 | 0 | None | 수급 안정화 대기 |

`SIGNAL 0 + HOLD_QTY > 0`은 위 표에 없는 상태다. 발생원은 두 곳뿐이다.

| 발생원 | 대응 |
|--------|------|
| `mapping_swing` 자동 등록 (`service.py:352-365`) | FR-01 (활성화 시 편입) |
| 분할매수 중 `partial_exec` Redis 키 소실 (`auto_swing_batch.py:547`, `405-411`) | FR-02 (배치 진입 시 편입) |

---

## 상세 설계

### 1. Entity — 포지션 편입 메서드

**파일**: `app/domain/swing/entity.py`

`transition_to_buy` 아래에 추가한다. Entity는 순수 도메인이므로 외부 조회 없이 인자로만 판단한다.

```python
def adopt_position(self, hold_qty: int, entry_price: float, current_price: float) -> None:
    """기존 보유 포지션을 자동매매 관리 대상으로 편입 (SIGNAL 0 -> 1)

    - 계좌 보유종목 자동 등록분(SWING_TYPE='S')을 활성화하는 경우
    - 분할매수 중간 상태가 Redis 소실로 고아가 된 경우

    transition_to_buy와 달리 '신규 매수'가 아니라 '이미 보유 중인 수량의 인수'이므로
    체결 이력을 남기지 않고 CUR_AMOUNT도 차감하지 않는다 (이미 매수된 자금).
    """
    if self.SIGNAL != 0:
        raise ValidationError(f"포지션 편입은 대기 상태(0)에서만 가능합니다. 현재: {self.SIGNAL}")
    if hold_qty <= 0:
        raise ValidationError("편입 수량은 1주 이상이어야 합니다")
    if not entry_price or entry_price <= 0:
        raise ValidationError("편입 평단가가 없어 손절/익절 기준을 세울 수 없습니다")

    self.SIGNAL = 1
    self.HOLD_QTY = hold_qty
    self.ENTRY_PRICE = to_price(entry_price)
    # PEAK는 평단을 하한으로 둔다 — 평단 아래로 잡히면 트레일링 익절 기준이 비정상적으로 낮아짐
    # (auto_swing_batch.py:331-335의 기존 패턴과 동일)
    self.PEAK_PRICE = to_price(max(current_price, entry_price))
    self.MOD_DT = datetime.now()


def clear_orphan_position(self) -> None:
    """실보유 0주 확인 시 잔여 수량 정보 정리 (SIGNAL 0 유지)"""
    self.HOLD_QTY = 0
    self.ENTRY_PRICE = None
    self.PEAK_PRICE = None
    self.MOD_DT = datetime.now()
```

**`transition_to_buy`는 변경하지 않는다.** 호출부가 `auto_swing_batch.py:544` 1곳이고
`is_waiting()` 가드 안에서만 실행되므로, 위 두 지점에서 불변식을 지키면 `HOLD_QTY>0`인 상태로
도달할 수 없다. (근거 상세는 Plan 3.2 참조)

---

### 2. Service — 활성화 시 편입 (FR-01)

**파일**: `app/domain/swing/service.py`

#### 2.1 증권사 실보유 조회 헬퍼

```python
async def _fetch_broker_position(self, user_id: str, mrkt_code: str, st_code: str) -> dict | None:
    """증권사 실보유 수량/평단 조회 (편입 기준값)

    DB의 HOLD_QTY는 매핑 시점 스냅샷이므로 신뢰하지 않는다.
    (그 사이 사용자가 증권사 앱에서 직접 매도/추가매수했을 수 있음)

    Returns:
        {"qty": int, "avg_price": float, "prpr": float} 또는 None (조회 실패)
    """
    try:
        if is_overseas(mrkt_code):
            holdings = await foreign_api.get_us_holdings(user_id, self.db)
        else:
            holdings = await get_stock_balance(user_id, self.db)
        for item in holdings.get("output1", []):
            if item.get("pdno") != st_code:
                continue
            return {
                "qty": int(float(item.get("hldg_qty", 0) or 0)),
                "avg_price": float(item.get("pchs_avg_pric", 0) or 0),
                "prpr": float(item.get("prpr", 0) or 0),
            }
        return {"qty": 0, "avg_price": 0.0, "prpr": 0.0}  # 보유 목록에 없음 = 0주
    except Exception as e:
        # 조회 실패로 활성화 자체를 막지는 않는다 (배치 진입 가드가 안전망)
        logger.error(f"[{mrkt_code}/{st_code}] 실보유 조회 실패 - 편입 보류: {e}", exc_info=True)
        return None
```

`foreign_api.get_stock_balance`가 해외 응답을 `pdno`/`hldg_qty`/`pchs_avg_pric`/`prpr` 키로
정규화하므로(`foreign_api.py:79-95`) 국내/해외가 동일 코드로 처리된다.

> **Plan 결정 수정**: Plan 4.2에서 "국내는 `get_available_capital`이 이미 호출한 `output1`을 재사용"으로
> 적었으나, `get_available_capital`은 `/swing/available-capital` 엔드포인트의 공개 반환 계약이다.
> 여기에 보유목록을 끼워넣으면 계약이 오염되므로 **전용 헬퍼로 재조회**한다.
> 활성화는 저빈도 사용자 액션이므로 호출 1회 추가는 수용한다.

#### 2.2 활성화 분기에 편입 연결

`update_swing`의 데이터 적재 트리거(`_ensure_stock_data`) 직전에 배치한다.
편입은 **DB 상태를 바꾸므로 별도 commit이 필요**하다.

```python
            result = await self.repo.update(swing_id, data)
            await self.db.commit()

            if is_activating and user_id:
                # 보유 수량이 남아있는 스윙(매핑 자동등록분)은 신규 진입이 아니라 포지션 편입 대상
                if (result.HOLD_QTY or 0) > 0 and result.SIGNAL == 0:
                    await self._adopt_position_on_activate(user_id, result)

            await self.db.refresh(result)   # ↓ 2.3 참조
            response = SwingResponse.model_validate(result).model_dump()

            if is_activating and user_id:
                response["DATA_STATUS"] = await self._ensure_stock_data(
                    user_id, result.MRKT_CODE, result.ST_CODE
                )
```

`repo.update`가 반환하는 인스턴스는 `find_by_id`로 이미 로드된 **같은 객체**이므로
(`result is swing` → True) 편입 대상으로 그대로 쓸 수 있다.

#### 2.3 응답 신선도 — 설계 중 발견한 기존 결함 (FR-05)

`repo.update`는 Core bulk UPDATE + `synchronize_session=False`이므로
identity map의 인스턴스가 갱신되지 않는다. sqlite 재현 결과:

```
load      : USE_YN=N
after upd : in-memory USE_YN=N   ← DB에는 'Y'가 기록됐지만 메모리는 그대로
result is swing : True
DB 최종   : USE_YN=Y SIGNAL=1 HOLD_QTY=100   ← 편입 변경은 정상 반영
```

즉 **현재 `PUT /swing/{id}/settings`는 변경 전 값을 응답으로 돌려준다.**
활성화를 눌러도 응답의 `USE_YN`이 `"N"`이다. 이 기능과 무관한 기존 결함이지만,
편입 결과(`SIGNAL=1`, `HOLD_QTY`)를 응답에 실어야 하는 FR-01과 같은 지점이므로 함께 고친다.

- 대응: 응답 직전 `await self.db.refresh(result)` 1회 (SELECT 1회 추가)
- in-memory가 stale하더라도 dirty tracking 덕분에 **stale 값이 DB로 되쓰이지는 않는다**
  (위 재현에서 `USE_YN=Y`가 유지됨) → 데이터 손상 위험은 없고 응답만 틀렸다

```python
async def _adopt_position_on_activate(self, user_id: str, swing) -> None:
    """활성화 시 기존 보유 포지션 편입 (실보유 기준)"""
    position = await self._fetch_broker_position(user_id, swing.MRKT_CODE, swing.ST_CODE)
    if position is None:
        return  # 조회 실패 — 편입 보류 (배치 진입 가드가 처리)

    if position["qty"] <= 0:
        # 매핑 후 사용자가 직접 전량 매도 → 잔여 수량 정보 정리하고 정상 매수대기
        swing.clear_orphan_position()
        logger.info(f"[{swing.ST_CODE}] 실보유 0주 - 잔여 수량 정리, 매수대기 유지")
    else:
        swing.adopt_position(
            hold_qty=position["qty"],
            entry_price=position["avg_price"],
            current_price=position["prpr"] or position["avg_price"],
        )
        logger.info(
            f"[{swing.ST_CODE}] 포지션 편입: {position['qty']}주 "
            f"@ {position['avg_price']:,.2f} → SIGNAL=1 (손절/익절 관리 시작)"
        )
    await self.db.commit()
```

편입은 `try/except ValidationError`로 감싼다. 증권사가 `qty>0`인데 평단 `0`을 반환하면
`adopt_position`이 `ValidationError`를 던지는데, 이는 `update_swing`의 `except SQLAlchemyError`를
통과해 **422**가 된다. 활성화는 이미 commit된 상태이므로 "실패로 보이지만 실제로는 활성화됨"
불일치가 생긴다. `adopt_position`은 검증을 mutate보다 먼저 하므로 rollback도 불필요하다.

---

### 3. Batch — 진입 가드 및 자기치유 (FR-02)

**파일**: `app/domain/swing/trading/auto_swing_batch.py`

`prev_signal = swing.SIGNAL` 캡처 **앞**(`=== 2-1 ===`)에 삽입한다. 주문 실행 전이므로
체결과 DB가 어긋날 여지가 없다.

> **위치 정정 (구현 중 변경)**: 최초 설계는 `=== 4. SIGNAL별 오케스트레이션 ===` 앞이었으나,
> 그 위치는 `prev_signal` 캡처 뒤라서 편입 시 `prev_signal=0` / `SIGNAL=1`이 되고
> `_fire_trade_notification`의 `new_signal == 1 and prev_signal == 0` 분기가 성립해
> **"매수 완료" 푸시를 오발신**한다. 편입은 체결이 아니므로 `prev_signal` 캡처 앞으로 옮긴다.
> 이 이동으로 새로 실행되는 코드는 `if swing.has_position(): update_peak_price()` 하나이며,
> `adopt_position`이 `PEAK = max(현재가, 평단) >= 현재가`로 세팅하므로 no-op이다.

> **⚠️ 아래 코드는 2026-09-17 변경으로 대체되었다.** 현재 구현은 DB값 대신
> `swing_service.fetch_broker_position()`으로 증권사 실보유를 조회해 편입한다.
> 변경 후 코드와 사유는 문서 말미 「후속 변경 상세」 참고.

```python
            # === 2-1. 불변식: SIGNAL 0 + HOLD_QTY>0 은 성립할 수 없다 ===  [구 버전]
            # 신규 진입으로 처리하면 transition_to_buy가 기존 수량/평단을 덮어써 포지션이 유실된다.
            if swing.is_waiting() and (swing.HOLD_QTY or 0) > 0:
                entry_price = float(swing.ENTRY_PRICE) if swing.ENTRY_PRICE else 0
                if entry_price <= 0:
                    logger.error(
                        f"[{st_code}] SIGNAL=0 + {swing.HOLD_QTY}주 보유 + 평단 없음 "
                        f"→ 편입 불가, 이번 사이클 스킵 (수동 확인 필요)"
                    )
                    return
                swing.adopt_position(
                    hold_qty=swing.HOLD_QTY,
                    entry_price=entry_price,
                    current_price=float(current_price),
                )
                logger.warning(
                    f"[{st_code}] 고아 포지션 편입: {swing.HOLD_QTY}주 @ {entry_price:,.2f} "
                    f"→ SIGNAL=1 (신규 매수 차단, 손절/익절 평가로 전환)"
                )
```

추가로, 가드보다 앞선 지표 캐시 확인 분기(`auto_swing_batch.py:188`)는 `has_position()`만 보므로
`SIGNAL=0 + HOLD_QTY>0`인 무방비 포지션을 warning으로 흘려보낸다. 조건에 `(swing.HOLD_QTY or 0) > 0`을
추가해 error로 올린다. (이 분기는 `return`하므로 매수는 나가지 않으나, 손절/익절도 평가되지 않는다)

편입 후 아래 분기 체인이 그대로 실행되어 `has_position()` 분기를 타므로
**같은 사이클에서 즉시 손절/익절이 평가된다.** 다음 사이클까지 무방비로 대기하지 않는다.

~~배치는 증권사 조회 없이 DB값으로 편입한다. 이 경로의 `HOLD_QTY`/`ENTRY_PRICE`는
분할체결 executor가 누적 계산해 저장한 값(`order_executor.py:533-538`)이므로 신뢰 가능하다.~~

> **정정 (2026-09-17)**: 위 근거는 발생원 두 개 중 Redis 키 소실분에만 해당한다.
> 나머지 발생원인 `mapping_swing` 자동등록분은 executor가 쓴 값이 아니라 매핑 시점 스냅샷이고,
> 이후 갱신되지 않아 활성화까지 몇 달 묵을 수 있다. 게다가 Redis 키가 소실됐다는 것은
> 체결 일부가 DB에 반영되지 못했을 수 있다는 뜻이기도 해서, 그 경로의 DB값도 무조건
> 신뢰 가능하다고 볼 수 없다. **두 발생원 모두 증권사를 기준값으로 쓰는 것이 맞다.**

---

### 4. 흐름 비교

```
[변경 전] 매핑 보유종목(100주, SIGNAL=0, CUR_AMOUNT=0) 활성화
  → 배치: is_waiting() → _handle_waiting → CUR_AMOUNT=0 → "매수 수량 부족" return
  → 손절/익절 평가 없음. 100주 방치.
  → INIT_AMOUNT 증액 시: 매수 실행 → HOLD_QTY 100 → 32 덮어쓰기 (포지션 유실)

[변경 후] 같은 스윙 활성화
  → service: 실보유 조회(100주 @70,000) → adopt_position → SIGNAL=1
  → 배치: has_position() → _handle_position → 손절/익절 평가 정상 수행
  → INIT_AMOUNT 증액해도 is_waiting()이 False → 신규 매수 경로 진입 불가
```

---

### 5. 테스트 계획 (FR-03)

**파일**: `tests/test_swing_mapped_adoption.py` (신규)

| 케이스 | 검증 내용 |
|--------|-----------|
| 매핑 스윙 활성화 (실보유 100주) | `SIGNAL 0→1`, `HOLD_QTY=100`, `PEAK_PRICE=max(현재가, 평단)` |
| 매핑 스윙 활성화 (실보유 0주) | `clear_orphan_position` 호출, `SIGNAL=0` 유지, `HOLD_QTY=0` |
| 실보유 조회 실패 | 활성화는 성공, 편입 보류, `SIGNAL=0` 유지 (fail-soft) |
| DB 100주 ≠ 실보유 150주 | 실보유 150주로 편입 (증권사 값이 단일 진실) |
| 배치: SIGNAL=0 + HOLD_QTY>0 | 매수 주문 0건, `SIGNAL=1` 편입, 같은 사이클에 `_handle_position` 진입 |
| 배치: SIGNAL=0 + HOLD_QTY>0 + 평단 없음 | 매수 주문 0건, 상태 변화 없음, error 로그 |
| 배치: DB 100주 ≠ 실보유 60주 *(2026-09-17 추가)* | 실보유 60주 / 증권사 평단으로 편입 (낡은 DB값 미사용) |
| 배치: 실보유 0주 *(2026-09-17 추가)* | `clear_orphan_position`, `SIGNAL=0` 유지, 매도 주문 0건 |
| 배치: 실보유 조회 실패 *(2026-09-17 추가)* | 상태 변화 없음, 주문 0건 (다음 사이클 재시도) |
| 회귀: SIGNAL=0 + HOLD_QTY=0 | 기존 정상 진입 경로와 동작 동일 (`transition_to_buy` 정상 호출) |
| 응답 신선도 (FR-05) | 활성화 응답의 `USE_YN='Y'`, 편입 시 `SIGNAL=1`/`HOLD_QTY`가 응답에 반영 |

기존 `tests/test_swing_trade_scenarios.py`, `tests/test_swing_entity.py` 전량 통과 유지.

---

## 구현 순서

1. `entity.py` — `adopt_position()`, `clear_orphan_position()` 추가
2. `auto_swing_batch.py` — 4-0 진입 가드 (단독으로 즉시 안전망 확보)
3. `service.py` — `_fetch_broker_position()`, `_adopt_position_on_activate()` + 활성화 분기 연결 + 응답 `refresh` (FR-05)
   *(2026-09-17: 배치가 호출자로 추가되며 `fetch_broker_position()`으로 공개 전환)*
4. `tests/test_swing_mapped_adoption.py` — 회귀 테스트 고정
5. 런타임 검증 — 스텁 기반 시나리오 실행 (증상 A/B 재현 스크립트 재사용)

---

## 알려진 제약 / 후속 과제

| 항목 | 내용 |
|------|------|
| 편입 종목의 재매수 자본 | 매핑 스윙은 `CUR_AMOUNT=0`이므로 편입 후 청산해도 매도대금만 자본이 된다. 추가 배정을 원하면 사용자가 `INIT_AMOUNT`를 조정해야 한다 (증액분이 `CUR_AMOUNT`에 반영됨) |
| `mapping_swing` merge 분기 | 기존 스윙의 `HOLD_QTY`를 상시 동기화하지 않는다. 활성화 시점 동기화로 필요 시점은 커버하나, 비활성 스윙의 DB값은 계속 스냅샷으로 남는다<br>**(2026-09-17 유지 결정)** 배치 가드까지 증권사를 기준값으로 쓰게 되어 이 스냅샷은 더 이상 편입 판단에 쓰이지 않는다. 남은 용도는 가드 진입 조건(`HOLD_QTY > 0`)과 목록 표시뿐이라 동기화 로직을 추가하지 않는다 — 추가하면 "DB를 신뢰하지 않는다"는 원칙이 흐려진다 |
| 가드가 도달하지 못하는 경로 | 지표 캐시 없음(`:188`)·미확인 주문 미해결(`:372`) 분기는 가드 앞에서 `return`한다. 두 경로 모두 매수를 실행하지 않으므로 포지션 유실은 없으나, 편입이 그만큼 지연된다 |
| ~~배치 가드의 DB값 신뢰~~ → **해소 (2026-09-17)** | ~~배치 편입은 증권사 조회 없이 DB값을 쓴다. 활성화 시 조회 실패 **AND** 사용자가 이미 전량 매도한 경우 팬텀 `SIGNAL=1`이 생겨 매도 주문 실패가 반복될 수 있다 (두 조건 동시 성립 필요, 확률 낮음)~~<br>배치 가드가 증권사 실보유를 조회하도록 변경해 해소. 실보유 0주면 편입 대신 `clear_orphan_position`으로 정리한다. 재평가 결과 이 항목은 Low가 아니었다 — 발생 확률은 낮지만 **자가 회복이 불가능**해(매도 경로에 실보유 검증이 없어 주문이 영구 거절) 수동 개입 없이는 풀리지 않는 종류였다 |
| `day_collect_job` 수집 범위 | `DATA_YN='Y'` 전체 유지. 활성 스윙으로 좁히려면 `_calculate_indicators`의 봉 신선도 검증이 선행 필요 (별건) |

---

## 후속 변경 상세 (2026-09-17)

### 배경

아카이브 시점 설계는 편입 기준값에 대해 두 가지를 동시에 주장하고 있었다.

- `fetch_broker_position` docstring: "DB의 `HOLD_QTY`는 매핑 시점 스냅샷이므로 신뢰하지 않는다"
- 배치 가드: DB의 `HOLD_QTY`/`ENTRY_PRICE`를 그대로 사용

편입 경로 중 **배치 가드만 이 원칙의 예외**였다. 이번 변경은 그 예외를 제거해 원칙과 구현을 일치시킨다.

### 변경 후 동작

```
SIGNAL=0 + HOLD_QTY>0 감지
   ↓ swing_service.fetch_broker_position()
조회 실패        → 스킵, 상태 불변 (다음 사이클 재시도)
실보유 0주       → clear_orphan_position() → SIGNAL=0 유지 (정상 매수대기)
실보유 N주       → adopt_position(증권사 수량/평단) → SIGNAL=1
평단 0 반환      → ValidationError 포착 → 스킵 (수동 확인 로그)
```

`prev_signal` 캡처 앞이라는 위치는 유지되어, 편입 0→1이 매수 완료 푸시로 오발신되지 않는다.

### 설계 판단 근거

| 검토안 | 채택 | 사유 |
|--------|:----:|------|
| 배치 가드에서 증권사 재조회 | ✅ | 백스톱 자체를 정확하게 만든다. 재조회는 가드 조건이 참일 때(= 이상 상태)만 발생하므로 틱당 API 호출 증가가 없다. 기존 설계 원칙("편입 실패가 활성화를 막지 않는다", "배치가 최종 백스톱")을 유지한다 |
| 편입 실패 시 `USE_YN` 롤백 | ❌ | 잘못된 상태의 *진입*만 막고 백스톱은 여전히 부정확하게 남는다. 이 문서가 명시한 설계 원칙을 뒤집는 방향 |
| `mapping_swing`에서 스냅샷 상시 동기화 | ❌ | 「알려진 제약」 참고 — 그 값이 더 이상 편입 판단에 쓰이지 않아 불필요하고, 원칙만 흐려진다 |

### 변경 파일

| 파일 | 변경 |
|------|------|
| `app/domain/swing/service.py` | `_fetch_broker_position` → `fetch_broker_position` 공개 (배치가 호출자로 추가됨) |
| `app/domain/swing/trading/auto_swing_batch.py` | 가드가 증권사 실보유로 편입 + 실보유 0주 분기(`clear_orphan_position`) 추가 |
| `tests/test_swing_trade_scenarios.py` | 페이크 `SwingService`에 `fetch_broker_position` 스텁 (기본값 = DB와 일치, 테스트가 `self.broker_position`으로 괴리 주입) |
| `tests/test_swing_mapped_adoption.py` | 괴리 3경로 테스트 추가 (수량 괴리 / 실보유 0 / 조회 실패) |

### 검증

- 전체 스위트 70 passed (변경 전 67 + 신규 3), 19 subtests
- red-green 대조: 앱 변경분만 stash한 구코드에서 신규 테스트 3건이 정확히 실패함을 확인
- **미수행**: KIS 실계좌 호출 검증. 아카이브 시점 후속 항목(실보유 조회 응답 형식 실측)이 그대로 남아 있으며, 이번 변경으로 **배치 경로도** 그 응답 형식에 의존하게 되어 실측 범위가 넓어졌다
