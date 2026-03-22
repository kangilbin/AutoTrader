# Design: trading-fix — 자동 매매 배치 안정성 개선

> **Plan 참조**: `docs/01-plan/features/trading-fix.plan.md`

## 변경 대상 파일

| 파일 | 변경 유형 | 이슈 |
|------|-----------|------|
| `app/domain/swing/trading/auto_swing_batch.py` | 구조 변경 | #1, #2, #3, #9 |
| `app/common/scheduler.py` | 스케줄 추가 | #3 |
| `app/domain/swing/trading/order_executor.py` | 로직 변경 | #4, #6 |
| `app/domain/swing/trading/strategies/single_ema_strategy.py` | 상수 변경 | #8 |
| `app/domain/swing/trading/trading_strategy_factory.py` | 정리 | #11 |

## 구현 순서

1단계 → 2단계 → 3단계 → 4단계 → 5단계 (순차 구현)

---

## 1단계: DB 세션 분리 + 롤백 (#1, #2)

### 1-1. `trade_job()` 변경

**현재:**
```python
async def trade_job():
    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        redis_client = await Redis.get_connection()
        swing_list = await swing_service.get_active_swings()
        tasks = [
            process_single_swing(swing_row, swing_service, redis_client)
            for swing_row in swing_list
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # ...
    except Exception as e:
        logger.error(...)
    finally:
        await db.close()
```

**변경 후:**
```python
async def trade_job():
    """매매 신호 확인 및 실행 (5분 단위)"""
    # 목록 조회 전용 세션 (읽기 전용, 빠르게 반환)
    db = await Database.get_session()
    try:
        swing_service = SwingService(db)
        redis_client = await Redis.get_connection()
        swing_list = await swing_service.get_active_swings()
        logger.info(f"[BATCH START] 활성 스윙 수: {len(swing_list)}")
    except Exception as e:
        logger.error(f"trade_job 스윙 목록 조회 실패: {e}", exc_info=True)
        return
    finally:
        await db.close()  # 목록 조회 세션 즉시 반환

    # 각 종목별 독립 세션으로 병렬 처리
    tasks = [
        process_single_swing(swing_row, redis_client)
        for swing_row in swing_list
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = sum(1 for r in results if not isinstance(r, Exception))
    error_count = len(results) - success_count
    logger.info(
        f"[BATCH END] 배치 작업 완료 - "
        f"성공: {success_count}, 실패: {error_count}, 총: {len(results)}"
    )
```

**핵심 변경점:**
- `swing_service`를 `process_single_swing`에 전달하지 않음
- 목록 조회용 세션은 `finally`에서 즉시 반환
- 각 종목 처리는 내부에서 개별 세션 생성

### 1-2. `process_single_swing()` 변경

**현재 시그니처:**
```python
async def process_single_swing(swing_row, swing_service: SwingService, redis_client):
```

**변경 후 시그니처:**
```python
async def process_single_swing(swing_row, redis_client):
```

**변경 후 전체 구조:**
```python
async def process_single_swing(swing_row, redis_client):
    """
    개별 스윙 매매 오케스트레이터 (독립 세션 + 세마포어)
    """
    async with _SEMAPHORE:
        db = await Database.get_session()       # ← 개별 세션
        try:
            swing_service = SwingService(db)     # ← 개별 서비스
            swing_id = swing_row.SWING_ID
            st_code = swing_row.ST_CODE
            user_id = swing_row.USER_ID if hasattr(swing_row, 'USER_ID') else None
            swing_type = swing_row.SWING_TYPE if hasattr(swing_row, 'SWING_TYPE') else 'S'

            swing = await swing_service.repo.find_by_id(swing_id)
            if not swing:
                logger.warning(f"[{swing_id}] 스윙 엔티티 로드 실패")
                return

            strategy = TradingStrategyFactory.get_strategy(swing_type)

            # === 1. 데이터 수집 === (기존과 동일)
            cached_indicators = await strategy.get_cached_indicators(redis_client, st_code)
            if not cached_indicators:
                logger.warning(f"[{st_code}] 등록된 캐시 정보가 없습니다.")
                return

            current_price_data = await get_inquire_price("mgnt", st_code, db)
            if not current_price_data:
                logger.warning(f"[{st_code}] 현재가 조회 실패")
                return

            # ... (current_price, current_high 등 파싱 — 기존과 동일)

            cached_indicators = TechnicalIndicators.enrich_cached_indicators_with_realtime(...)
            avg_daily_amount = cached_indicators["avg_daily_amount"]

            # === 2. 부분 체결 진행 중 체크 === (기존과 동일, db 변수만 로컬)
            # ...

            # === 3. PEAK_PRICE 갱신 === (기존과 동일)
            # ...

            prev_signal = swing.SIGNAL

            # === 4. SIGNAL별 오케스트레이션 === (기존과 동일)
            # ...

            # === 5. 변경사항 저장 ===
            await db.flush()
            await db.commit()

            # === 6. 푸쉬 알림 ===
            if user_id and swing.SIGNAL != prev_signal:
                _fire_trade_notification(user_id, swing, prev_signal, st_code)

        except Exception as e:
            await db.rollback()                  # ← #2 해결: 롤백 추가
            logger.error(
                f"스윙 처리 실패 (SWING_ID={swing_row.SWING_ID}, ST_CODE={swing_row.ST_CODE}): {e}",
                exc_info=True
            )
        finally:
            await db.close()                     # ← 세션 반환 보장
```

### 1-3. `_handle_signal_*` 함수 — 변경 없음

기존 `_handle_signal_0`, `_handle_signal_1`, `_handle_signal_2`, `_handle_signal_3` 함수들은 파라미터로 `db`를 받고 있으므로 시그니처 변경 불필요. `process_single_swing`에서 로컬 `db`를 전달하면 됨.

### 1-4. DB 커넥션 풀 검증

**현재 설정 (config.py):**
```python
DB_POOL_SIZE: int = 10
DB_MAX_OVERFLOW: int = 20
```

세마포어 `_SEMAPHORE = asyncio.Semaphore(5)` → 동시 최대 5개 세션.
`trade_job`(+목록 조회 1개) + `day_collect_job`(1개) 고려해도 총 7개 이내.
**pool_size=10이면 충분. 변경 불필요.**

---

## 2단계: 매매 시간 확장 (#3)

### 2-1. `scheduler.py` 변경

**현재:**
```python
scheduler.add_job(
    trade_job,
    CronTrigger(minute='*/5', hour='10-14', day_of_week='mon-fri')
)
```

**변경 후:**
```python
# 스윙 트레이딩 배치 작업: 평일 10시-15시20분, 5분마다 실행
scheduler.add_job(
    trade_job,
    CronTrigger(minute='*/5', hour='10-14', day_of_week='mon-fri')
)

# 장 마감 전 추가 실행 (15:00~15:20, 5분마다)
scheduler.add_job(
    trade_job,
    CronTrigger(minute='0,5,10,15,20', hour='15', day_of_week='mon-fri')
)
```

**설계 결정: 왜 `hour='10-15'`가 아닌 별도 등록인가?**
- `hour='10-15'` → 15:25, 15:30 ... 15:55까지 불필요한 실행 발생
- KIS API 주문 접수 마감(~15:25) 이후 실행 시 매번 에러 발생 + 불필요한 DB/API 호출
- 별도 등록으로 15:20까지만 명시적으로 제한

### 2-2. `auto_swing_batch.py` — 변경 없음

`trade_job`에 `exit_only` 파라미터를 추가하지 않음. 15:00~15:20에도 매수/매도 모두 허용 (사용자 피드백 반영).

---

## 3단계: Redis-DB 상태 순서 변경 (#4)

### 3-1. `order_executor.py` — `execute_buy_with_partial` 변경

**현재 (Redis 먼저):**
```python
# line 127-134
partial_state = {...}
await redis_client.setex(f"partial_exec:{swing_id}", 86400, json.dumps(partial_state))  # Redis 먼저
return {"success": True, "completed": False, ...}
```

**변경 후 (Redis 저장을 caller에 위임):**
```python
# Redis 상태를 직접 저장하지 않고, 반환값에 포함
return {
    "success": True,
    "completed": False,
    "qty": executed_qty,
    "avg_price": avg_price,
    "amount": executed_amount,
    "phase": signal_on_complete,
    "partial_state": {                  # ← 새 필드: caller가 DB commit 후 저장
        "type": "buy",
        "phase": signal_on_complete,
        "target_amount": float(target_amount),
        "executed_amount": executed_amount,
    }
}
```

### 3-2. `order_executor.py` — `execute_sell_with_partial` 동일 패턴 적용

```python
return {
    "success": True,
    "completed": False,
    "qty": actual_qty,
    "phase": signal_on_complete,
    "partial_state": {                  # ← 새 필드
        "type": "sell",
        "phase": signal_on_complete,
        "target_qty": target_qty,
        "executed_qty": actual_qty,
    }
}
```

### 3-3. `auto_swing_batch.py` — 각 signal 핸들러에서 Redis 저장

`_handle_signal_0` 예시:
```python
order_result = await SwingOrderExecutor.execute_buy_with_partial(...)

if not order_result.get("success"):
    return

# Entity 상태 업데이트 (기존과 동일)
avg_price = order_result.get("avg_price", int(current_price))
qty = order_result.get("qty", 0)
if order_result.get("completed", True):
    swing.transition_to_first_buy(avg_price, qty, int(current_price))
else:
    swing.ENTRY_PRICE = Decimal(avg_price)
    swing.HOLD_QTY = qty
    swing.MOD_DT = datetime.now()

# 거래 내역 저장 (기존과 동일)
trade_service = TradeHistoryService(db)
await trade_service.record_trade(...)

# DB commit (process_single_swing의 최상위에서 수행됨 — 여기서는 flush만)
await db.flush()

# Redis 저장은 process_single_swing의 commit 성공 후에 처리
# → order_result에 partial_state가 있으면 process_single_swing에서 저장
```

`process_single_swing`에 Redis 저장 로직 추가:
```python
# === 5. 변경사항 저장 ===
await db.flush()
await db.commit()

# === 5-1. 부분 체결 Redis 상태 저장 (DB commit 성공 후) ===
if hasattr(swing, '_pending_partial_state') and swing._pending_partial_state:
    await redis_client.setex(
        f"partial_exec:{swing.SWING_ID}", 86400,
        json.dumps(swing._pending_partial_state)
    )
    swing._pending_partial_state = None
```

> **대안**: `_pending_partial_state`를 Entity 속성으로 쓰는 대신, `_handle_signal_*` 함수의 반환값으로 전달하는 방식도 가능. 하지만 현재 핸들러가 반환값이 없으므로 Entity에 임시 속성을 붙이는 것이 변경 범위가 작음.

### 3-4. `continue_partial_execution` — 변경 동일 패턴

Redis 삭제(`redis_client.delete(partial_key)`)도 DB commit 후로 이동:
```python
# 완료 시: Redis 삭제를 반환값으로 위임
return {
    "completed": True,
    ...,
    "clear_partial": True  # ← caller가 commit 후 Redis 삭제
}
```

`process_single_swing`의 부분 체결 처리 블록:
```python
if partial_result.get("completed") or partial_result.get("aborted"):
    # ... Entity 상태 업데이트
    await db.flush()
    await db.commit()

    # DB commit 성공 후 Redis 정리
    if partial_result.get("clear_partial", True):
        await redis_client.delete(partial_key)
    return
```

---

## 4단계: 체결 확인 보강 (#6)

### 4-1. `order_executor.py` — 체결 확인 재시도

**적용 위치:** `execute_buy_with_partial`, `execute_sell_with_partial`, `continue_partial_execution` 내 모든 `check_order_execution` 호출부

**현재:**
```python
execution = await check_order_execution(user_id, order_no, db)
executed_qty = execution.get("executed_qty", qty) if execution else qty
```

**변경 후:**
```python
execution = await _check_execution_with_retry(user_id, order_no, db)
if not execution:
    # 체결 확인 불가 → 보수적 처리
    logger.warning(f"[{st_code}] 체결 확인 불가 (주문번호: {order_no})")
    return {
        "success": True,
        "completed": False,
        "qty": 0,
        "avg_price": 0,
        "order_no": order_no,
        "unconfirmed": True
    }
executed_qty = execution.get("executed_qty", qty)
avg_price = execution.get("avg_price", int(curr_price))
```

### 4-2. 공통 헬퍼 함수 추가 (order_executor.py 하단)

```python
async def _check_execution_with_retry(
    user_id: str, order_no: str, db, max_retries: int = 2, delay: float = 1.0
):
    """체결 확인 재시도 (최대 2회, 1초 간격)"""
    for attempt in range(max_retries):
        execution = await check_order_execution(user_id, order_no, db)
        if execution and execution.get("executed_qty", 0) > 0:
            return execution
        if attempt < max_retries - 1:
            await asyncio.sleep(delay)
    return None
```

**설계 결정:**
- `max_retries=2`: 총 2회 시도 (초회 + 1회 재시도). 5분 사이클 내에 충분
- `delay=1.0`: 1초 대기. KIS API 체결 반영 시간 고려
- 미확인 시 `qty=0`으로 반환 → 다음 사이클에서 `continue_partial_execution`이 재확인
- `unconfirmed=True` 플래그로 caller에서 구분 가능

---

## 5단계: MEDIUM/LOW 일괄 수정 (#8, #9, #11)

### 5-1. Redis TTL 확장 (#8)

**파일:** `single_ema_strategy.py`

**현재 (2곳):**
```python
await redis_client.setex(prev_state_key, 900, json.dumps(new_state))   # line 285
await redis_client.setex(prev_state_key, 1800, json.dumps(new_state))  # line 331 — 이미 수정됨?
```

확인 필요. 두 곳 모두 `setex(..., 1800, ...)` 로 통일:
```python
# check_entry_signal 내 (line 285, 331)
ENTRY_STATE_TTL = 1800  # 30분 (5분 × 6사이클)
await redis_client.setex(prev_state_key, ENTRY_STATE_TTL, json.dumps(new_state))
```

### 5-2. 알림 예외 처리 (#9)

**파일:** `auto_swing_batch.py` — `_fire_trade_notification` 함수

**현재:**
```python
asyncio.create_task(
    PushNotificationService.send_trade_notification(...)
)
```

**변경 후:**
```python
task = asyncio.create_task(
    PushNotificationService.send_trade_notification(...)
)
task.add_done_callback(_on_notification_done)
```

함수 추가:
```python
def _on_notification_done(task: asyncio.Task):
    """알림 태스크 완료 콜백 — 예외 로깅"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.warning(f"푸쉬 알림 전송 실패: {exc}")
```

### 5-3. 전략 팩토리 정리 (#11)

**파일:** `trading_strategy_factory.py`

**현재:**
```python
_strategies: dict[str, Type[TradingStrategy]] = {
    # 'A': SingleEMAStrategy,
    # 'B': SingleEMAStrategy,
    'S': SingleEMAStrategy,
}
```

**변경 후:**
```python
_strategies: dict[str, Type[TradingStrategy]] = {
    'S': SingleEMAStrategy,  # 단일 20EMA 전략 (현재 유일한 실전 전략)
}
```

주석 처리된 'A', 'B' 라인 삭제. 필요 시 별도 전략 클래스 구현 후 등록.

---

## 수정하지 않는 항목 (유지)

| # | 이슈 | 사유 |
|---|------|------|
| #5 | 재진입 자본 초과 | 오분석 — 매도 현금에서 재진입하므로 정상 |
| #7 | PEAK_PRICE 5분 지연 | 설계적 한계, ATR 배수가 지연 흡수 |
| #10 | OBV ddof=1 | 의도적 설계, 백테스트와 일관성 유지 |
| #12 | SELL_RATIO 절삭 | 주식 정수 단위 특성상 정상 |

---

## 영향 분석

### 변경되는 함수 시그니처

| 함수 | 현재 | 변경 후 |
|------|------|---------|
| `trade_job()` | 파라미터 없음 | 파라미터 없음 (내부 구조만 변경) |
| `process_single_swing()` | `(swing_row, swing_service, redis_client)` | `(swing_row, redis_client)` |

### 변경되는 반환값

| 함수 | 추가 필드 |
|------|-----------|
| `execute_buy_with_partial` | `partial_state: dict` (분할 시) |
| `execute_sell_with_partial` | `partial_state: dict` (분할 시) |
| `continue_partial_execution` | `clear_partial: bool` (완료 시) |

### 외부 영향

- **스케줄러**: `trade_job`의 시그니처가 변경되지 않으므로 `scheduler.py`의 `add_job(trade_job, ...)` 호출 변경 없음
- **다른 도메인**: `SwingService`, `SwingRepository` 변경 없음
- **API 엔드포인트**: 배치 전용 변경이므로 Router/API 영향 없음

---

## 테스트 계획

| 항목 | 검증 방법 |
|------|-----------|
| DB 세션 독립성 | 동시 2개 이상 종목 처리 시 커밋 격리 확인 |
| 롤백 동작 | 의도적 예외 발생 후 다른 종목 정상 처리 확인 |
| 15:00~15:20 실행 | 스케줄러 로그로 실행 시점 확인 |
| 부분 체결 순서 | Redis 상태가 DB commit 후에만 저장되는지 확인 |
| 체결 재시도 | KIS API 지연 시 재시도 로그 확인 |
| 커넥션 풀 | 5개 동시 세션 + 여유분 확인 (pool_size=10) |