# 오더블럭(Order Block) 스윙 매매 전략 — 설계서

> 상태: **Design (구현 전)** · 작성 2026-10-24
> 기획: [`docs/01-plan/features/order-block-strategy.plan.md`](../../01-plan/features/order-block-strategy.plan.md) — 매매 규칙·수치는 기획서가 기준이다. 이 문서는 **어디에 어떻게 넣는가**를 정한다.

---

## 0. 설계 중 확인한 기존 코드 사실 (설계에 영향)

| # | 사실 | 위치 | 설계 반영 |
|---|------|------|----------|
| F1 | 부분익절 시 `transition_to_partial`이 **PEAK_PRICE를 None으로 리셋**한다. 다음 사이클 현재가로 다시 시작. 반면 단일 이평선 **백테스트는 리셋하지 않는다** (실전·백테스트 불일치) | `entity.py` `transition_to_partial`, `auto_swing_batch.py` 지연체결 경로 `PEAK_PRICE = current_price` | **D1로 리셋 제거 완료** — 기획서 4.2와 일치 |
| F2 | 체결 미확인 매수가 다음 사이클에 확인되면 `transition_to_buy`를 거치지 않고 `swing.SIGNAL = new_signal`로 직접 전이한다 | `auto_swing_batch.py` pending 처리 `else` 분기 | 손절가를 `pending_order`에 실어 두고 확인 시 기록 (§6.3) |
| F3 | `find_active_stock_codes`는 `(MRKT_CODE, ST_CODE)`만 돌려준다 — SWING_TYPE을 모름 | `repository.py` | OB 탐지 대상 조회 메서드 추가 (§7) |
| F4 | 백테스트 `ceil_tick`/`floor_tick`은 **국내 호가표만** 있다. 미국 $150 종목도 1원 단위 표가 적용돼 **정수 달러로 올림/내림**된다 | `backtest/strategies/base_strategy.py` | 손절폭이 좁은 OB 전략엔 치명적 ($1 오차 vs R $3). OB 백테스트는 시장별 호가 사용. 단일 이평선 수정 여부는 **결정 D2** |
| F5 | `SwingTrade.validate`가 SWING_TYPE을 `('S','A','B')`로 제한 | `entity.py` | `'O'` 추가 |
| F6 | 배치는 `enrich_cached_indicators_with_realtime`에 당일 고가·저가를 넘기지만 **결과 dict에 남기지 않는다**. 당일 시가는 아예 안 읽는다 | `auto_swing_batch.py` | 배치가 `today_open`/`today_low`를 dict에 추가 (§6.1) |
| F7 | 매수 수량은 배치가 `strategy.entry_pct(full_entry, adx, obv_z)`로 직접 계산 — 손절가를 받을 자리가 없다 | `_handle_waiting` | 전략에 `calc_entry_qty` 위임 (§5.2) |
| F8 | 장 전 워밍업은 활성 종목 전부의 `indicators:{st_code}`(전일 완성봉 ATR 포함)를 만든다 | `service.warmup_ema_cache` | OB 전략은 ATR을 **이 캐시에서** 읽는다 — OB 캐시에 ATR을 중복 저장하지 않음 |

### "사용 이력" 저장이 따로 필요 없는 이유 (기획서 2.2 보완)

기획서 2.2의 "사용 이력(이 OB로 이미 매수)"은 **미터치 규칙이 자연히 처리한다.** 매수는 반드시 당일 저가 ≤ 근접선일 때 일어나므로, 다음 날 워밍업에서 그 OB는 "터치됨"으로 소멸한다.
남는 구멍은 **같은 날 재진입**(손절 → 쿨다운 3→4→0이 장중에 다 지나감)뿐이라, 이것만 Redis `ob_used:{swing_id}`(당일 TTL)로 막는다.

---

## 1. 구현 단계와 게이트

| Phase | 내용 | 끝나는 조건 |
|-------|------|------------|
| **1. 탐지 + 백테스트** | `order_block.py`, `base_order_block.py`, `order_block_backtest_strategy.py`, 백테스트 팩토리 `'O'`, 시장별 호가 | 단위 테스트 통과 + **실제 종목 백테스트 결과 검토 (사용자 확인 게이트)** |
| 2. DB | `STOP_PRICE` 컬럼, 엔티티 생명주기 | ALTER 실행, entity 테스트 |
| 3. 실전 연동 | `order_block_strategy.py`, 배치·executor·워밍업·팩토리 `'O'`, `describe()` | 시나리오 테스트 + 모의투자 |
| 4. 문서 | CLAUDE.md 전략 표, strategies README | |

Phase 1 결과가 기대에 못 미치면 Phase 2 이후는 보류하고 기획서 규칙을 먼저 고친다.

---

## 2. 파일 구조

```
app/domain/swing/
├── order_block.py                              # [신규] OB 탐지 순수 함수 (pandas만, I/O 없음) — 실전·백테스트 공용
├── entity.py                                   # [수정] STOP_PRICE, 생명주기, validate 'O'
├── repository.py                               # [수정] find_active_stock_codes_by_type
├── service.py                                  # [수정] 워밍업·단건 캐싱에 OB 캐시 추가
├── trading/
│   ├── auto_swing_batch.py                     # [수정] today_open/low, calc_entry_qty, stop_price 전달
│   ├── order_executor.py                       # [수정] execute_buy(pending_extra)
│   ├── trading_strategy_factory.py             # [수정] 'O' 등록
│   └── strategies/
│       ├── base_trading_strategy.py            # [수정] calc_entry_qty 기본 구현, stop_price 파라미터
│       ├── single_ema_strategy.py              # [수정] 시그니처만 (stop_price 무시) — 동작 불변
│       ├── base_order_block.py                 # [신규] 상수 + 순수 계산 (손절·수량·청산선·목표가)
│       └── order_block_strategy.py             # [신규] 실전 신호 + describe()
└── backtest/
    ├── backtest_service.py                     # [수정] params["mrkt_code"]
    ├── strategy_factory.py                     # [수정] 'O' 등록
    └── strategies/
        ├── base_strategy.py                    # [수정] 시장별 호가 헬퍼
        └── order_block_backtest_strategy.py    # [신규]
tests/
├── test_order_block_detection.py               # [신규]
├── test_order_block_backtest.py                # [신규]
├── test_order_block_strategy.py                # [신규] 실전 신호·청산선
├── test_strategy_description.py                # [수정] OB describe 수치-상수 연동
└── test_swing_entity.py                        # [수정] STOP_PRICE 생명주기
```

---

## 3. `order_block.py` — 탐지 (순수 함수)

### 3.1 데이터 구조

```python
@dataclass(frozen=True)
class OrderBlock:
    ob_idx: int          # OB 캔들 행 위치 (df 정렬 기준)
    bos_idx: int         # BOS 봉 행 위치 — 이 봉이 완성돼야 OB가 존재한다
    ob_date: str         # YYYYMMDD (캐시·로그 식별자)
    bos_date: str
    proximal: float      # 근접선 (PROXIMAL_SOURCE 반영)
    distal: float        # 원격선 = OB 캔들 저가
```

### 3.2 함수

```python
def confirmed_pivot_highs(high: np.ndarray, pivot_len: int) -> list[tuple[int, int]]:
    """[(pivot_idx, confirmed_idx)] — confirmed_idx = pivot_idx + pivot_len
    high[j] > 좌우 pivot_len봉 모두 (동률은 피벗 아님)"""

def detect_order_blocks(df: pd.DataFrame, params: OBParams) -> list[OrderBlock]:
    """df 전체에서 품질 조건을 통과한 OB 후보 전부 (bos_idx 오름차순)
    필요 컬럼: STCK_BSOP_DATE, STCK_OPRC, STCK_HGPR, STCK_LWPR, STCK_CLPR, ACML_VOL, atr, avg_vol20, ema120"""

def active_order_block(df: pd.DataFrame, candidates: list[OrderBlock],
                       as_of: int, params: OBParams) -> OrderBlock | None:
    """as_of 행(마지막 완성봉)까지의 정보만으로 활성 OB 1개 (가장 최근 bos)"""
```

`OBParams`는 `BaseOrderBlockStrategy`의 상수를 묶은 frozen dataclass — 함수가 전략 클래스를 import하지 않게 한다 (순수 함수 유지, 테스트에서 파라미터만 바꿔 검증).

### 3.3 BOS·OB 규칙 (기획서 2.1의 모호한 부분 확정)

| 항목 | 확정 규칙 |
|------|----------|
| 기준 피벗 | 봉 k에서 `confirmed_idx ≤ k−1`인 피벗 중 **가장 최근 것** |
| BOS 1회성 | 한 피벗은 **처음으로 종가가 넘은 봉**에서만 BOS. 이후 봉이 또 넘어도 새 BOS 아님 |
| OB 탐색 | `k−1`부터 `max(0, k−OB_SEARCH_BARS)`까지 역순, `close < open`인 첫 봉 |
| 임펄스 구간 | `ob_idx+1 … k` (거래량 조건은 이 구간 최대값) |
| 품질 지표 시점 | `atr`, `avg_vol20`, `ema120`은 **봉 k의 값** (k는 완성봉이므로 선견 아님) |
| 중복 | 서로 다른 BOS가 같은 OB 캔들을 가리키면 **먼저 생긴 BOS** 하나만 |
| NaN | 품질 지표가 NaN이면 그 후보 제외 (워밍업 구간) |

### 3.4 활성 판정 (`active_order_block`)

```
후보 중  bos_idx ≤ as_of  이고  as_of − bos_idx ≤ OB_MAX_AGE  인 것에 대해
    구간 = (bos_idx+1 … as_of)          # bos_idx == as_of 면 빈 구간 → 활성 (BOS 다음 날 첫 리테스트 가능)
    touched = 구간 저가 최소 ≤ proximal
    터치 안 됐으면 활성
활성 중 bos_idx 최대 1개 반환 (없으면 None)
```

- (구현 시 변경) 종가 붕괴(`종가 < distal`) 조건은 두지 않는다 — distal ≤ proximal이므로 붕괴는 항상 터치를 포함한다.
- (구현 시 변경) `bos_idx < as_of` → `≤`. BOS 봉이 완성된 뒤라 선견이 아니고, BOS 다음 날 되돌림도 정상적인 첫 리테스트다.

- BOS 봉 자체(`bos_idx`)는 터치 판정에서 제외한다 — 임펄스 첫날 저가가 존 안일 수 있다.
- **백테스트**: 봉 i 판정 시 `as_of = i−1`. 후보는 `detect_order_blocks`를 전체 df에 한 번만 돌려 만들고, `bos_idx ≤ i−1`인 것만 고려하므로 선견이 없다 (k의 피벗 확정·품질 지표 모두 k 이전 정보).
- **실전 워밍업**: `as_of = 마지막 행`(전일 완성봉).

---

## 4. `base_order_block.py` — 상수와 계산

기획서 8장 상수를 그대로 클래스 상수로 둔다. 순수 계산만 classmethod로 두고 실전·백테스트가 공유한다 (단일 이평선의 `BaseSingleEMAStrategy.calculate_exit_line` 패턴).

```python
class BaseOrderBlockStrategy:
    # 8장 상수 …  + 쿨다운 상수는 BaseSingleEMAStrategy와 같은 값 (배치 _handle_cooling_down이 strategy.COOLDOWN_* 를 읽음)

    @classmethod
    def ob_params(cls) -> OBParams: ...

    @classmethod
    def stop_price(cls, distal: float, atr: float) -> float:
        """distal − STOP_ATR_BUFFER × atr"""

    @classmethod
    def stop_pct_ok(cls, entry: float, stop: float) -> bool:
        """0 < (entry − stop)/entry×100 ≤ MAX_STOP_LOSS_PCT"""

    @classmethod
    def entry_qty(cls, cur_amount: float, price: float, stop: float, full_entry: bool) -> int:
        """전량 모드: int(cur_amount × FULL_ENTRY_PCT / price)
        그 외: min(int(cur_amount × RISK_PER_TRADE / (price − stop)), int(cur_amount × MAX_ENTRY_PCT / price))"""

    @classmethod
    def calculate_exit_line(cls, entry: float, peak: float, stop: float, atr: float) -> float:
        """R = entry − stop.  exit = stop
           peak ≥ entry + BREAKEVEN_R×R  → max(exit, entry)
           peak ≥ entry + TRAIL_ACTIVATE_R×R and atr>0 → max(exit, peak − TRAIL_ATR_MULT×atr)
           R ≤ 0 이면 stop 반환 (구현 시 변경: 0을 돌리면 실전에서 영영 팔지 않는다)"""

    @classmethod
    def exit_stage(cls, entry, peak, stop) -> str:   # "손절" | "본전방어" | "이익확정"

    @classmethod
    def partial_target(cls, entry: float, stop: float) -> float:
        """entry + PARTIAL_TP_R × (entry − stop)"""

    @classmethod
    def fallback_stop(cls, entry: float) -> float:
        """STOP_PRICE 없을 때: entry × (1 − MAX_STOP_LOSS_PCT/100)"""
```

R은 **실제 평단(ENTRY_PRICE) − STOP_PRICE**로 매번 계산한다. 신호 시 현재가와 체결가가 달라도 손절가는 고정이고 R만 체결가 기준으로 바뀐다.

---

## 5. 실전 전략 `order_block_strategy.py`

```python
class OrderBlockStrategy(TradingStrategy, BaseOrderBlockStrategy):
    name = "오더블럭"
    CONSECUTIVE_REQUIRED = 2
    ENTRY_STATE_TTL = 1800
    TRADE_CYCLE_MINUTES = 5
```

### 5.1 `get_cached_indicators`

1. `SingleEMAStrategy.get_cached_indicators(redis, symbol)`에 위임 — `indicators:{st_code}`는 전략과 무관한 공용 워밍업 산출물이고, 필수/선택 키 검증 로직을 복제하지 않기 위함. 없으면 None (배치가 "포지션 무방비" 에러 로그 처리).
2. `ob_zone:{st_code}`를 읽어 `indicators["ob_zone"]`에 넣는다. **없거나 파싱 실패여도 None을 반환하지 않는다** — OB는 매수에만 필요하고 청산은 STOP_PRICE·ATR로 동작해야 한다.

> 대안: 공용 로더를 `indicators.py`로 추출. 단일 이평선 코드를 건드리게 되어 이번 범위에서 제외 (**결정 D3**).

### 5.2 `check_entry_signal` (시그니처는 기존 그대로)

```
ob = cached_indicators.get("ob_zone")             → 없으면 None (연속성 리셋 없이 반환)
atr = cached_indicators["atr"]                    # 전일 완성봉
low, open_ = cached_indicators["today_low"], ["today_open"]

# 장중 붕괴 표시 (당일 1회 확인되면 그날 무효)
if curr < ob.distal: setex ob_broken:{symbol} (당일 TTL) → 리셋 후 None
if exists ob_broken:{symbol} → 리셋 후 None
if open_ < ob.distal  → 리셋 후 None                # 갭 붕괴
if get ob_used:{swing_id} == ob.ob_date → None     # 같은 날 재진입 방지

stop = stop_price(ob.distal, atr)
current_signal = (low ≤ ob.proximal
                  and ob.distal < curr ≤ ob.proximal + ENTRY_CHASE_ATR × atr
                  and stop_pct_ok(curr, stop))
연속성: entry:{swing_id} (단일 이평선과 같은 형식·TTL)
충족 시 → {"action": "BUY", "price": curr, "stop_price": stop, "ob_date": ob.ob_date,
          "reasons": ["오더블럭", f"존 {distal}~{proximal}", f"손절 {stop}"]}
```

- 연속 2회 사이 손절가가 달라질 수 있다(ATR은 하루 고정이므로 실제론 같다). 반환은 **마지막 판정값**.
- `ob_broken`·`ob_used` TTL은 `market_cache_ttl(mrkt_code)`와 같은 "다음 워밍업까지" 기준을 쓴다 — 전략은 mrkt_code를 모르므로 `cached_indicators["date"]`(전일 날짜)를 키에 넣어 날짜가 바뀌면 자연 무효: `ob_broken:{symbol}:{date}`, `ob_used:{swing_id}:{date}`, TTL 24h.

### 5.3 수량 — `calc_entry_qty` (TradingStrategy 신규 훅)

```python
# base_trading_strategy.py
@classmethod
def calc_entry_qty(cls, cur_amount, price, full_entry, cached_indicators, entry_result) -> int:
    """기본 구현 = 기존 배치 식 (단일 이평선 동작 그대로 이동)"""
    pct = cls.entry_pct(full_entry, cached_indicators.get('realtime_adx', 0), cached_indicators.get('realtime_obv_z', 0))
    return int(cur_amount * pct / price) if price > 0 else 0

# order_block_strategy.py
@classmethod
def calc_entry_qty(cls, cur_amount, price, full_entry, cached_indicators, entry_result) -> int:
    return cls.entry_qty(cur_amount, price, entry_result["stop_price"], full_entry)
```

배치 로그의 `투입비=` 표기는 `qty × price / cur_amount`로 바꿔 전략과 무관하게 계산한다.

### 5.4 `check_exit_signal` / `check_partial_take_profit`

두 메서드에 `stop_price: float | None = None` 키워드 인자를 추가한다 (베이스·단일 이평선은 받기만 하고 무시).

```
stop = stop_price or fallback_stop(entry)   # None이면 warning 로그 (편입·구버전 포지션)
atr  = cached_indicators["atr"]             # 완성봉
peak = max(peak_price or 0, entry, curr)
exit = calculate_exit_line(entry, peak, stop, atr)
curr ≤ exit → SELL, reasons=[exit_stage, f"청산선 {exit}", f"손익 {pct:+.1f}%"]

부분익절: signal == 1 and curr ≥ partial_target(entry, stop) → SELL_HALF
```

ATR ≤ 0이어도 **손절(단계 1·2)은 동작**해야 한다 — 단일 이평선은 ATR 0이면 HOLD로 스킵하지만, OB의 시작 청산선은 ATR과 무관하다. 트레일링(단계 3)만 생략.

### 5.5 `describe()`

기획서 9장 문장을 상수로 조립. 키는 `REQUIRED_KEYS` 9개. 테스트에서 `STOP_ATR_BUFFER`, `PARTIAL_TP_R`을 patch해 문장이 따라 바뀌는지 확인.

---

## 6. 배치·executor 변경 (`auto_swing_batch.py`, `order_executor.py`)

### 6.1 당일 시가·저가 노출 (F6)

현재가 파싱 직후, enrich 호출 뒤:

```python
cached_indicators["today_open"] = float(current_price_data.get("open" if _overseas else "stck_oprc", 0) or 0)
cached_indicators["today_low"]  = float(current_low)
```

단일 이평선은 읽지 않으므로 동작 불변.

### 6.2 `_handle_waiting`

| 현재 | 변경 |
|------|------|
| `entry_pct = strategy.entry_pct(...)`; `target_qty = int(equity × entry_pct / price)` | `target_qty = strategy.calc_entry_qty(equity, price, full_entry, cached_indicators, entry_result)` |
| `execute_buy(...)` | `execute_buy(..., pending_extra={"stop_price": entry_result.get("stop_price")})` |
| `swing.transition_to_buy(avg, qty, price)` | `swing.transition_to_buy(avg, qty, price, stop_price=entry_result.get("stop_price"))` |
| — | 체결 성공 시 `entry_result.get("ob_date")`가 있으면 `ob_used:{swing_id}:{date}` 기록 |

유동성 필터·실보유 대조(`_adopt_before_buy`)는 순서·동작 그대로.

### 6.3 지연 체결 매수 (F2)

```python
# pending 처리, completed 분기
elif ... 기존 else 분기:
    swing.SIGNAL = new_signal
    if pending_result.get("exec_type") == "buy" and pending.get("stop_price"):
        swing.STOP_PRICE = to_price(pending["stop_price"])
```

`execute_buy`는 `pending_extra` 인자를 받아 `_place_and_confirm`에 그대로 넘긴다 (현재는 `{}` 고정).
결과 미상 주문(`OrderOutcomeUnknownError`) 경로도 같은 `pending_order` dict를 쓰는지 구현 시 확인한다.

### 6.4 `_handle_position`

`check_exit_signal(..., stop_price=_f(swing.STOP_PRICE))`, `check_partial_take_profit(..., stop_price=...)`.

### 6.5 바꾸지 않는 것

- `enrich_cached_indicators_with_realtime` 호출 (BaseSingleEMAStrategy 상수) — OB도 `realtime_obv_z`(쿨다운)만 쓴다.
- `_handle_cooling_down` — `strategy.COOLDOWN_*`만 읽는다.
- 매도 실행·실보유 대조·알림.

---

## 7. 워밍업·캐시 (`service.py`, `repository.py`)

### 7.1 조회

```python
# repository.py
async def find_active_stock_codes_by_type(self, swing_type: str) -> List[tuple]:
    """USE_YN='Y' AND SWING_TYPE=swing_type 인 고유 (MRKT_CODE, ST_CODE)"""
```

### 7.2 `warmup_ema_cache` 끝에 OB 캐시

```
ob_codes = find_active_stock_codes_by_type('O')  (scope 필터 동일 적용)
for (mrkt, st) in ob_codes:
    df = 3년 일봉 + prepare_indicators_from_df (ema120·atr·avg_vol20 포함)
    ob = active_order_block(df, detect_order_blocks(df, P), as_of=len(df)-1, P)
    ob 있으면 setex ob_zone:{st} (market_cache_ttl) {"ob_date","bos_date","proximal","distal","as_of_date"}
    없으면   delete ob_zone:{st}          # 어제 존이 남아 오늘 매수하면 안 된다
```

- `_calculate_indicators`가 이미 3년 일봉을 읽는다 → 같은 df를 재사용하도록 내부 헬퍼를 분리 (DB 조회 2회 방지).
- `cache_single_indicators`(스윙 등록·활성화 시)도 SWING_TYPE `O`면 OB 캐시를 함께 만든다.
- 실패 시 `ob_zone` 키 삭제 + warning — 매수만 멈추고 청산은 영향 없음.

### 7.3 Redis 키 정리

| 키 | 값 | TTL | 작성 | 읽기 |
|----|----|-----|------|------|
| `indicators:{st_code}` | 기존 | 기존 | 워밍업 | 모든 전략 (ATR) |
| `ob_zone:{st_code}` | 활성 OB JSON | `market_cache_ttl` | 워밍업 | OB 매수 |
| `ob_broken:{st_code}:{date}` | "1" | 24h | OB 매수 판정 | OB 매수 |
| `ob_used:{swing_id}:{date}` | ob_date | 24h | 배치 매수 체결 후 | OB 매수 |
| `entry:{swing_id}` | 연속성 (기존 형식) | 1800s | 매수 판정 | 매수 판정 |

---

## 8. 엔티티·DB (`entity.py`)

```python
STOP_PRICE = Column(DECIMAL(15, 2), nullable=True, comment='매수 시 확정 손절가 (오더블럭 전략, 그 외 NULL)')
```

| 메서드 | STOP_PRICE |
|--------|-----------|
| `transition_to_buy(entry, qty, peak, stop_price=None)` | `to_price(stop_price)` 또는 None |
| `adopt_position` | None (OB 근거 없음 → 폴백) |
| `reset_cycle` / `clear_orphan_position` | None |
| `transition_to_partial` | 유지 (PEAK도 유지 — D1) |
| `validate` | SWING_TYPE `('S','A','B','O')` |

```sql
ALTER TABLE SWING_TRADE
ADD COLUMN STOP_PRICE DECIMAL(15, 2) NULL COMMENT '매수 시 확정 손절가 (오더블럭 전략, 그 외 NULL)'
AFTER PEAK_PRICE;
```

배포 순서: **ALTER 실행 → 코드 배포.** (ORM이 SELECT에 STOP_PRICE를 넣으므로 반대 순서면 모든 스윙 조회가 실패)

---

## 9. 백테스트 `order_block_backtest_strategy.py`

### 9.1 준비

- `params["mrkt_code"]` 추가 (`backtest_service.run_backtest`) — 호가 단위 결정.
- `base_strategy.py`에 `buy_tick(price, mrkt_code)`/`sell_tick(price, mrkt_code)`: 해외면 `normalize_order_price`, 국내면 기존 `ceil_tick`/`floor_tick` (F4).
- 지표: `TechnicalIndicators.prepare_indicators_from_df(df, ema_long=TREND_EMA_PERIOD, ...)` — `ema120, atr, avg_vol20, obv_z` 사용.
- 후보: `detect_order_blocks(df, P)` 1회.
- `min_bars(params)` = `TREND_EMA_PERIOD + 1`.

### 9.2 봉 i 처리 순서

```
prev = i−1 봉,  atr = prev.atr  (전일 완성봉)

[보유 중: signal 1/2]
  peak = max(peak, prev.high)
  exit = sell_tick(calculate_exit_line(entry, peak, stop, atr))
  low ≤ exit         → 전량 매도 @ sell_tick(min(exit, open)) → signal 3, continue
  signal==1 and high ≥ target → 50% @ sell_tick(max(target, open)) → signal 2, continue

[쿨다운: signal 3/4]  단일 이평선 백테스트와 동일 (row.obv_z)

[대기: signal 0]
  ob = active_order_block(df, cands, as_of=i−1)
  없음 → continue
  open < ob.distal   → stats.gap_invalid[mrkt] += 1, continue
  low > ob.proximal  → continue
  buy = buy_tick(min(open, ob.proximal))
  stop = stop_price(ob.distal, atr);  stop_pct_ok(buy, stop) 아니면 continue
  qty = entry_qty(capital, buy, stop, full_entry);  qty ≤ 0 → continue
  매수 → signal 1, entry=buy, peak=buy, used.add(ob.ob_idx)
  같은 봉 low ≤ stop → 즉시 전량 매도 @ sell_tick(stop), reasons ["손절","진입봉"] → signal 3
  (같은 봉 부분익절은 판정하지 않는다 — 장중 순서 불명, 보수적)
```

- `used`는 같은 봉/다음 봉에 같은 OB가 다시 잡히는 경우를 막는 안전장치 (미터치 규칙상 실제로는 다음 봉부터 터치 처리됨).
- 체결가가 근접선 이하로 강제되므로 실전의 추격 제한(`+0.25ATR`)은 재현하지 않는다 — 기획서 7장 "의도적 차이".

### 9.3 결과 확장 (기획서 7장 검증 항목)

`_format_result` 결과에 `strategy_stats` 키를 추가한다 (다른 전략 응답은 불변).

```json
"strategy_stats": {
  "ob_detected": 14, "entries": 6,
  "gap_invalidations": 2,
  "days_to_1r": [3, 7, null, 12, 2, null],
  "exit_stages": {"손절": 2, "본전방어": 1, "이익확정": 3},
  "avg_r_multiple": 0.84,
  "proximal_source": "high"
}
```

- `days_to_1r`: 매수 후 고가가 entry+1R에 처음 닿은 거래일 수, 못 닿고 청산이면 null.
- 시장별 집계는 백테스트가 종목 단위라 `mrkt_code`를 함께 남기고, 비교는 여러 종목 결과를 모아서 한다.
- `"body"` 비교는 `PROXIMAL_SOURCE`를 바꿔 재실행 (API 파라미터로 노출하지 않음 — 튜닝용).

### 9.4 거래 비용

단일 이평선 백테스트와 같은 상수(수수료 0.147%, 거래세 0.20%)를 쓴다. 미국 종목엔 맞지 않는 값이지만 두 전략 비교가 같은 조건이어야 하므로 이번엔 유지 (기존 한계).

---

## 10. 테스트 계획

| 파일 | 케이스 |
|------|-------|
| `test_order_block_detection.py` | 피벗은 j+2봉 전엔 확정 안 됨 / BOS는 피벗당 1회 / 마지막 음봉 선택 / 변위·거래량·존폭·EMA120 각각 미달 시 제외 / 터치·붕괴·만료 시 비활성 / BOS 봉 저가는 터치 판정 제외 / 가장 최근 OB 선택 / `as_of` 이후 데이터를 바꿔도 결과 불변 (**선견 회귀 테스트**) / `PROXIMAL_SOURCE="body"` |
| `test_order_block_backtest.py` | 합성 시나리오: 근접선 체결가 / 갭 붕괴 무진입 / 진입봉 손절 / 2R 부분익절 → 본전·트레일링 청산 / 손절폭 7% 초과 무진입 / R 사이징 수량·80% 상한·전량 모드 / 미국 종목 호가 0.01 |
| `test_order_block_strategy.py` | 연속 2회 후 BUY + stop_price / 원격선 이탈 시 당일 무효 / ob_used 재진입 차단 / 청산선 3단계 / STOP_PRICE None 폴백 / ATR 0에서도 손절 동작 |
| `test_strategy_description.py` | `'O'` 등록 시 REQUIRED_KEYS / 상수 patch → 문장 반영 |
| `test_swing_entity.py` | STOP_PRICE 기록·유지(부분익절)·초기화(reset/orphan/adopt) / validate `'O'` |
| 기존 전체 | 단일 이평선 동작 불변 — 특히 `test_swing_trade_scenarios.py` |

---

## 11. 결정 필요

| # | 항목 | 선택지 | 권장 |
|---|------|-------|------|
| D1 | 부분익절 시 PEAK 리셋 (F1) | **확정·적용 (2026-10-24): 리셋 제거** — `transition_to_partial`과 지연 체결 경로의 PEAK 덮어쓰기 삭제 | 단일 이평선에선 부분익절 직후 5분 하락분만큼 청산선을 우연히 낮출 뿐 역할이 없고, OB에선 부분익절(2R)=트레일링 시작(2R)이라 직후 소폭 하락에도 트레일링이 꺼져 청산선이 본전까지 내려간다. 별도 커밋 |
| D2 | 백테스트 미국 호가 (F4) | **확정·적용 (2026-10-24): (a)** — `base_strategy.buy_tick/sell_tick(price, mrkt_code)`, `params["mrkt_code"]`, 단일 이평선 백테스트 교체 | 실제 종목 재실행 결과 PATH(저가주) +13.34% → −7.42%, PGY +9.05% → +14.23%. 저가 미국 종목일수록 결과가 크게 바뀐다. OB 백테스트는 처음부터 이 헬퍼를 쓴다 |
| D3 | 지표 캐시 로더 (5.1) | (a) `SingleEMAStrategy.get_cached_indicators` 위임 / (b) 공용 함수로 추출 | **(a)** 이번 범위. 세 번째 전략이 생기면 (b) |
