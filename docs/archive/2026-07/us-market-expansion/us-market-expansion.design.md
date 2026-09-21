# Design: 미국장 전 거래소(NYS/NAS/AMS) 확장 및 시장코드 매핑

> Plan: [`docs/01-plan/features/us-market-expansion.plan.md`](../../01-plan/features/us-market-expansion.plan.md)

## 설계 원칙
1. **정식코드 = NYS/NAS/AMS** (시세계열 3글자). STOCK_INFO·SWING_TRADE·클라이언트·도메인 계층 전부 이 값만 사용.
2. **매핑은 `foreign_api` 경계에서만.** 도메인/배치는 항상 정식코드를 넘기고, KIS 코드 변환은 `foreign_api`가 쿼리 구성 시점에 수행.
   - 시세계열 `EXCD` = 정식코드 그대로 (매핑 없음)
   - 거래계열 `OVRS_EXCG_CD` = `to_ovrs_excg_cd(정식코드)` → NYSE/NASD/AMEX
3. **해외 판별**은 `== "NASD"` 문자열 비교 → `is_overseas(mrkt_code)` 로 전면 교체.

---

## 0. 계층 위반 정리: `Order`/`ModifyOrder` → `app/core/order.py`

기존에 external(`foreign_api.py`, `kis_api.py`)이 `app.domain.order.entity` 를 import → **external → domain 역방향 위반**. 정착 방향은 `domain → external`. `Order`/`ModifyOrder` 는 (코드 주석대로) ORM 없는 KIS 파라미터 검증 DTO이자 domain·external 공용이므로, 최하위 공유계층 `app/core/order.py` 로 이동한다.

| 작업 | 내용 |
|------|------|
| 이동 | `app/domain/order/entity.py` 의 `Order`, `ModifyOrder` → `app/core/order.py` (내용 동일, docstring "공유 파라미터 DTO"로 조정) |
| 제거 | `app/domain/order/entity.py` 삭제 |
| import 갱신 (생성측·domain) | `order/service.py:7`, `swing/trading/order_executor.py:11` → `from app.core.order import Order, ModifyOrder` |
| import 갱신 (소비측·external) | `foreign_api.py:13`, `kis_api.py:16` → `from app.core.order import Order, ModifyOrder` |

> 결과: external → `app.core` 만 참조(정방향). `Order` 의 `validate()`(입력검증)는 공유커널에 두어도 무방.
> **범위 밖(별도 PDCA)**: `kis_api.py:17` `from app.domain.auth.repository import AuthRepository` 도 external → domain 위반이나, `_get_user_auth` 구조 변경이 필요해 이번 작업에서 제외.

---

## 1. 신규 모듈: `app/core/market_code.py`

양 계층 공용 상수/헬퍼. external·domain 모두 `app.core` 참조 → 정방향.

```python
# app/core/market_code.py
"""미국장 시장코드 정식값 및 KIS API 그룹별 매핑"""

# 정식코드 (STOCK_INFO/SWING_TRADE 저장값, 클라이언트 전달값, 시세계열 EXCD)
US_MARKETS = ("NYS", "NAS", "AMS")

# 거래계열(주문·정정취소·미체결·잔고) OVRS_EXCG_CD 매핑
_EXCG_TRADE = {"NYS": "NYSE", "NAS": "NASD", "AMS": "AMEX"}

# 모의투자 잔고: 미국전체 미지원 → 거래소별 순회 대상
US_TRADE_EXCG = ("NASD", "NYSE", "AMEX")


def is_overseas(mrkt_code: str) -> bool:
    return mrkt_code in US_MARKETS


def to_ovrs_excg_cd(mrkt_code: str) -> str:
    """정식코드 → 거래계열 거래소코드. 이미 4글자면 그대로 통과."""
    return _EXCG_TRADE.get(mrkt_code, mrkt_code)
```

---

## 2. `app/domain/swing/entity.py` — VALID_MRKT_CODES 확장

```python
# before
VALID_MRKT_CODES = ('J', 'NX', 'UN', 'NASD')
# after
VALID_MRKT_CODES = ('J', 'NX', 'UN', 'NYS', 'NAS', 'AMS')
```

---

## 3. `app/external/foreign_api.py` — 매핑 경계 + 잔고 래퍼

### 3-1. 시세계열: EXCD 하드코딩 제거 → 파라미터화 (정식코드 그대로)

| 함수 | before | after |
|------|--------|-------|
| `get_inquire_price` | 인자 없음, `"EXCD": "NAS"` | 인자 `excd: str` 추가, `"EXCD": excd` |
| `get_stock_data` | 인자 없음, `"EXCD": "NAS"` | 인자 `excd: str` 추가, `"EXCD": excd` |
| `get_inquire_asking_price` | 인자 없음, `"EXCD": 'NAS'` | 인자 `excd: str` 추가, `"EXCD": excd` |
| `get_volume_rank` | 인자 없음, `"EXCD": "NAS"` | 인자 `excd: str = "NAS"` 추가, `"EXCD": excd` |
| `get_target_price` | `excd: str = "NAS"` (이미 O) | 유지 |
| `get_fluctuation_rank` | `excd: str = "NAS"` (이미 O) | 유지 |
| `get_volume_power_rank` | `excd: str = "NAS"` (이미 O) | 유지 |

> 시세계열은 정식코드(NYS/NAS/AMS)가 곧 EXCD 이므로 변환 함수 불필요. 호출부가 `mrkt_code` 를 그대로 `excd` 로 전달.

예) `get_inquire_price`:
```python
async def get_inquire_price(user_id: str, code: str, db: AsyncSession, excd: str = "NAS"):
    ...
    query = {"AUTH": "", "EXCD": excd, "SYMB": code}
```

### 3-2. 거래계열: OVRS_EXCG_CD 변환

`from app.core.market_code import to_ovrs_excg_cd, US_TRADE_EXCG`

| 함수 | before | after |
|------|--------|-------|
| `place_order_api` | `"OVRS_EXCG_CD": order.excg_cd` | `"OVRS_EXCG_CD": to_ovrs_excg_cd(order.excg_cd)` |
| `modify_or_cancel_order_api` | `order.excg_cd if hasattr... else "NASD"` | `to_ovrs_excg_cd(getattr(order,'excg_cd','NAS'))` |
| `get_inquire_daily_ccld_obj` | `excg_cd: str = "NASD"`, `"OVRS_EXCG_CD": excg_cd` | 인자는 정식코드 받고 `"OVRS_EXCG_CD": to_ovrs_excg_cd(excg_cd)` |
| `check_order_execution` | `excg_cd` 전달 | 정식코드 그대로 `get_inquire_daily_ccld_obj` 로 전달(내부 변환) |

> 도메인 계층은 계속 정식코드(NAS/NYS/AMS)를 `order.excg_cd`/`excg_cd` 로 넘긴다. 변환은 여기서 1회.

### 3-3. 잔고 래퍼 `get_us_holdings` 신규 (sim 분기)

```python
async def get_us_holdings(user_id: str, db: AsyncSession):
    """미국 전 거래소 보유종목 조회.
    실전: NASD(미국전체) 1회. 모의: 거래소별 순회 후 output1 병합."""
    _, access_data = await _get_user_auth(user_id, db)
    sim = access_data.get("simulation_yn") == "Y"

    if not sim:
        return await get_stock_balance(user_id, db, excg_cd="NASD")  # 미국전체

    merged = []
    for excg in US_TRADE_EXCG:          # ("NASD","NYSE","AMEX")
        r = await get_stock_balance(user_id, db, excg_cd=excg)
        merged.extend(r["output1"])
        await asyncio.sleep(0.3)        # 초당 거래건수 제한 회피
    return {"output1": merged, "output2": {}}
```

> `get_stock_balance` 의 `excg_cd` 는 이미 KIS 거래소코드(NASD 등)를 직접 받으므로 이 래퍼 내부에서는 변환 없이 그대로 사용. `output2` 는 소비처(`mapping_swing`)가 미사용하므로 빈 dict.

---

## 4. 해외 판별 `== "NASD"` → `is_overseas()` 전면 교체

`from app.core.market_code import is_overseas`

| 파일:라인 | before | after |
|-----------|--------|-------|
| `order/service.py:82` | `request.MRKT_CODE == "NASD"` | `is_overseas(request.MRKT_CODE)` |
| `order/service.py:85` | `excg_cd=request.MRKT_CODE if is_overseas else ""` | `excg_cd=request.MRKT_CODE if is_overseas(...) else ""` (정식코드 전달) |
| `swing/service.py:71` | `mrkt_code == "NASD"` | `is_overseas(mrkt_code)` |
| `swing/service.py:241` | `mrkt_code == "NASD"` | `is_overseas(mrkt_code)` |
| `swing/service.py:262` | `foreign_api.get_stock_balance(user_id, self.db)` | `foreign_api.get_us_holdings(user_id, self.db)` ★ |
| `swing/service.py:555,557` | `m == "NASD"` / `m != "NASD"` | `is_overseas(m)` / `not is_overseas(m)` |
| `swing/repository.py:46` | 조인 `SwingTrade.MRKT_CODE == Stock.MRKT_CODE` | 유지 (MRKT_CODE 통일로 정상 동작) |
| `swing/repository.py:54,55` | `mrkt_code == "NASD"` / `== "NASD"` | `is_overseas(mrkt_code)` 로 분기, 해외 시 `MRKT_CODE.in_(US_MARKETS)` |
| `swing/repository.py:81` | `MRKT_CODE IN ('J','NAS')` (버그) | 국내 세트로 정정: `MRKT_CODE IN ('J','NX','UN')` |
| `swing/repository.py:93` | `MRKT_CODE = 'NASD'` | `MRKT_CODE IN ('NYS','NAS','AMS')` |
| `swing/repository.py:148,150` | `== "NASD"` / `!= "NASD"` | `.in_(US_MARKETS)` / `.notin_(US_MARKETS)` |
| `swing/trading/order_executor.py:90,179,304,378` | `mrkt_code == "NASD"` + `excg_cd=mrkt_code` | `is_overseas(mrkt_code)` + `excg_cd=mrkt_code`(정식코드 유지, 변환은 foreign_api) |
| `swing/trading/order_executor.py:445` | `excg_cd=mrkt_code` | 유지 (정식코드) |
| `swing/trading/auto_swing_batch.py:137,699` | `mrkt_code == "NASD"` | `is_overseas(mrkt_code)` |
| `swing/trading/auto_swing_batch.py:703` | `excd = "NAS"` (하드코딩) | `excd = mrkt_code` (정식코드) |
| `swing/trading/auto_swing_batch.py:145` | `foreign_api.get_inquire_price(user_id, st_code, db)` | `...(user_id, st_code, db, excd=mrkt_code)` |
| `stock/repository.py:30,32` | `== 'NASD'` / `!= 'NASD'` | `.in_(US_MARKETS)` / `.notin_(US_MARKETS)` |
| `stock/stock_data_batch.py:36,107` | `mrkt_code == "NASD"` | `is_overseas(mrkt_code)` |
| `stock/stock_data_batch.py:108` | `foreign_api.get_stock_data(...)` | `excd=mrkt_code` 인자 추가 |

★ = 동작 변경(잔고 래퍼 교체), 나머지는 판별 방식만 교체.

---

## 5. 거래시간 설정 dict (US 3거래소 동일 시간)

`swing/service.py:33` `_MARKET_CLOSE_CONFIG`, `auto_swing_batch.py:43` `_MARKET_OPEN_CONFIG` 의 `"NASD"` 키가 정식코드와 불일치하게 됨.

**방식**: US 3코드를 동일 설정으로 확장 (헬퍼 도입보다 단순, 조회부 무변경).

```python
# swing/service.py
_US_CLOSE = {"close": time(16, 0), "tz": "America/New_York"}
_MARKET_CLOSE_CONFIG = {
    "J": {"close": time(16, 0), "tz": "Asia/Seoul"},
    "NYS": _US_CLOSE, "NAS": _US_CLOSE, "AMS": _US_CLOSE,
}
# auto_swing_batch.py
_US_OPEN = {"open": dt_time(9, 30), "tz": "America/New_York"}
_MARKET_OPEN_CONFIG = {
    "J": {...}, "NX": {...}, "UN": {...},
    "NYS": _US_OPEN, "NAS": _US_OPEN, "AMS": _US_OPEN,
}
```

`stock_data_batch.py:36` 의 `is_market_open_now(mrkt_code)` 는 `is_overseas(mrkt_code)` 분기로 교체(라인 36).

---

## 6. 클라이언트 계약 (라우터/스키마 문구)

값 검증은 entity(`VALID_MRKT_CODES`)에서 하므로 라우터는 문구/분기만.

| 위치 | 변경 |
|------|------|
| `stock/router.py:26,38,54,69,84` | Query description `"J:국내, NASD:나스닥"` → `"J:국내, NYS/NAS/AMS:미국(뉴욕/나스닥/아멕스)"` |
| `stock/router.py:41,57,72,87` | `if mrkt_code == "NASD"` → `if is_overseas(mrkt_code)` |
| `stock/router.py:42` | `get_inquire_asking_price(user_id, st_code, db)` → `..., excd=mrkt_code)` |
| `stock/router.py:58` | `get_fluctuation_rank(user_id, db, rank_sort)` → `..., excd=mrkt_code)` |
| `stock/router.py:73` | `get_volume_rank(user_id, db)` → `..., excd=mrkt_code)` |
| `stock/router.py:88` | `get_volume_power_rank(user_id, db)` → `..., excd=mrkt_code)` |
| `order/entity.py:19` | 주석 `(NASD)` → `(정식코드 NYS/NAS/AMS, 변환은 foreign_api)` |
| `order/schemas.py:22` | 주석 `(J, NX, UN, NASD)` → `(J, NX, UN, NYS, NAS, AMS)` |
| `swing/router.py:36,48` | Query description 갱신 |

---

## 7. DB 마이그레이션

실행 전 SELECT 로 잔존값 확인 후 UPDATE. (별도 `.sql` 스크립트 or 수동 실행)

```sql
-- 1) 확인
SELECT MRKT_CODE, COUNT(*) FROM SWING_TRADE GROUP BY MRKT_CODE;
SELECT MRKT_CODE, COUNT(*) FROM STOCK_DAY_HISTORY GROUP BY MRKT_CODE;

-- 2) 통일 (기존 해외 스윙은 전부 나스닥 → NAS)
UPDATE SWING_TRADE SET MRKT_CODE = 'NAS' WHERE MRKT_CODE = 'NASD';
-- STOCK_DAY_HISTORY 에 'NASD' 잔존 시에만
UPDATE STOCK_DAY_HISTORY SET MRKT_CODE = 'NAS' WHERE MRKT_CODE = 'NASD';
```

> STOCK_INFO 는 이미 NYS/NAS/AMS 재적재 완료 (확인만).

---

## 구현 순서 (청크 = 설명→diff→accept)
0. `Order`/`ModifyOrder` → `app/core/order.py` 이동 + import 4곳 갱신 + `domain/order/entity.py` 삭제
1. `app/core/market_code.py` 신규
2. `swing/entity.py` VALID_MRKT_CODES
3. `foreign_api.py` (시세 EXCD 파라미터화 → 거래 OVRS 변환 → `get_us_holdings`)
4. 판별 교체: order → swing(service/repository) → trading(order_executor/auto_swing_batch) → stock(repository/router/stock_data_batch)
5. 거래시간 dict 확장
6. 클라이언트 문구
7. DB 마이그레이션 실행
8. 실전/모의 계정 검증

## 검증 기준 (Plan 승계)
- [ ] NYS·NAS·AMS 주문/정정취소/미체결 (OVRS_EXCG_CD=NYSE/NASD/AMEX) 성공
- [ ] 시세/호가/순위 EXCD=정식코드 정상
- [ ] 실전 잔고 NASD 1회 / 모의 잔고 3거래소 병합
- [ ] `mapping_swing` 조인 정상, 잔존 `== "NASD"`/하드코딩 `"NAS"` 없음(매핑 경계 제외)
- [ ] SWING_TRADE `MRKT_CODE='NASD'` 잔존 0
