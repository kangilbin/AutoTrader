# Plan: 미국장 전 거래소(NYS/NAS/AMS) 확장 및 시장코드 매핑

## 개요
현재 미국장 로직은 나스닥 단일(`"NASD"`) 전용이다. 이를 **뉴욕(NYS)·나스닥(NAS)·아멕스(AMS) 전체**로 확장한다.
정식 시장코드를 **시세계열 3글자(NYS/NAS/AMS)** 로 통일해 STOCK_INFO·SWING_TRADE·클라이언트가 동일 값을 공유하고, KIS API가 요구하는 서로 다른 코드 체계는 **API 그룹별 매핑**으로 변환한다.

## 문제 정의

### 현재 구조
미국장 전체를 단일 코드 `"NASD"` 하나로 취급하며, 세 계층에 값이 흩어져 있다.

| 용도 | 현재 값 | 위치 |
|------|--------|------|
| STOCK_INFO 저장값 | **NYS/NAS/AMS** (재적재 완료) | `stock/entity.py` |
| SWING_TRADE 저장값 / 해외 판별자 | **NASD** 고정 | `swing/entity.py:11` + 전 코드 `== "NASD"` |
| 시세 API(EXCD) | **"NAS" 하드코딩** | `foreign_api.py` 6곳 |
| 거래 API(OVRS_EXCG_CD) | **"NASD"** | 주문/정정/미체결/잔고 |

### 문제점
1. **NYS/AMS 매매 불가**: 판별·거래소 코드가 나스닥 전용.
2. **저장값 불일치**: STOCK_INFO는 `NAS`인데 SWING_TRADE는 `NASD`.
   - `swing/repository.py:46` 의 `SwingTrade.MRKT_CODE == Stock.MRKT_CODE` 조인이 해외에서 깨진다.
   - `swing/repository.py:81` 만 `IN ('J','NAS')` — 나머지는 `'NASD'` (죽은 조건).
3. **API별 코드 체계 상이**: 같은 거래소라도 API 그룹마다 코드가 다름(아래 매핑 표).

## 변경 사항

### 정식코드(Canonical) = NYS / NAS / AMS
클라이언트가 종목명과 함께 보유·전달하는 값. STOCK_INFO·SWING_TRADE 모두 이 값으로 통일.

### API 그룹별 매핑 (확정)

| API 그룹 | 대상 API | 파라미터 | NYS | NAS | AMS |
|----------|----------|----------|-----|-----|-----|
| 시세계열 | 현재가·기간시세·체결가·호가·등락률/거래량/체결강도 순위 | `EXCD` | NYS | NAS | AMS (= 정식코드, 매핑 불필요) |
| 거래계열 | 주문·정정취소·미체결·잔고 | `OVRS_EXCG_CD` | NYSE | NASD | AMEX |
| 잔고(실전 미국전체) | 해외주식 잔고 | `OVRS_EXCG_CD` | — | **NASD = 미국전체** | — |

```python
# app/domain/stock/market_code.py (신규 — 양 계층 공용)
US_MARKETS = ("NYS", "NAS", "AMS")
EXCG_TRADE = {"NYS": "NYSE", "NAS": "NASD", "AMS": "AMEX"}   # 거래계열 OVRS_EXCG_CD

def is_overseas(mrkt_code: str) -> bool:      # 기존 == "NASD" 전부 이걸로 교체
    return mrkt_code in US_MARKETS

def to_ovrs_excg_cd(mrkt_code: str) -> str:   # 거래계열 변환
    return EXCG_TRADE.get(mrkt_code, mrkt_code)
```

**매핑은 `foreign_api` 경계에서만 수행한다.** 도메인 계층(order/swing/batch)은 항상 정식코드(NYS/NAS/AMS)를 전달하고, `foreign_api`가 쿼리 구성 시점에 EXCD(그대로) 또는 OVRS_EXCG_CD(`to_ovrs_excg_cd`)로 변환한다.

### 모의투자 잔고 처리 (사용자 결정: **sim 분기**)
`get_stock_balance`의 소비처(`mapping_swing`)는 `output1`만 사용(평가합계는 output1 재계산, 현금은 별도 `get_foreign_margin`)하므로 output2 병합 불필요.

- **실전**: `OVRS_EXCG_CD="NASD"`(미국전체) → 1회 호출 (현행 유지)
- **모의**: `["NASD","NYSE","AMEX"]` 순회 → 각 `output1` 병합 (3회, 항목별 `ovrs_excg_cd` 로 구분)

```python
# foreign_api.py 신규 래퍼
async def get_us_holdings(user_id, db):
    if 실전:  return await get_stock_balance(user_id, db, excg_cd="NASD")   # 미국전체 1회
    merged = []
    for excg in ("NASD", "NYSE", "AMEX"):        # 모의: 거래소별
        r = await get_stock_balance(user_id, db, excg_cd=excg)
        merged.extend(r["output1"]); await asyncio.sleep(0.3)   # 초당 제한 회피
    return {"output1": merged, "output2": {}}
```
→ 외부 신규매수 종목의 전 거래소 자동발견(신규 스윙 자동등록)을 실전·모의 모두 보존.

### 데이터 마이그레이션 (사용자 결정: **전면 통일**)
- `UPDATE SWING_TRADE SET MRKT_CODE='NAS' WHERE MRKT_CODE='NASD';`
  (기존 해외 스윙은 전부 나스닥이므로 NAS 로 확정)
- **STOCK_DAY_HISTORY 확인**: `MRKT_CODE='NASD'` 잔존 행이 있으면 `'NAS'` 로 동일 UPDATE (재적재로 이미 NAS 면 불필요 — 실행 전 SELECT 로 확인).

## 구현 순서

1. **`app/domain/stock/market_code.py`** (신규) — `US_MARKETS`, `EXCG_TRADE`, `is_overseas`, `to_ovrs_excg_cd`
2. **`app/domain/swing/entity.py`** — `VALID_MRKT_CODES = ('J','NX','UN','NYS','NAS','AMS')`
3. **`app/external/foreign_api.py`**
   - 시세계열: `EXCD` 하드코딩 `"NAS"` → 파라미터 `excd`(정식코드) 사용 (현재가·기간시세·호가·거래량순위 등 6곳)
   - 거래계열: 주문/정정취소/미체결 — `OVRS_EXCG_CD = to_ovrs_excg_cd(정식코드)` 로 변환
   - 잔고: `get_us_holdings` 래퍼 추가(sim 분기)
4. **`== "NASD"` 판별 전면 교체 → `is_overseas()`**
   - `order/service.py:82`, `order/entity` 사용부
   - `swing/service.py:71,241,555,557`
   - `swing/repository.py:46(조인),54,55,81,93,148,150`
   - `swing/trading/order_executor.py:90,179,304,378,445` — `excg_cd`는 정식코드 그대로 전달(변환은 foreign_api가 수행)
   - `swing/trading/auto_swing_batch.py:137,699`
   - `stock/repository.py:30,32`
   - `stock/router.py:41,57,72,87`
   - `stock/stock_data_batch.py:36,107`
5. **거래시간 dict** — `swing/service.py:35`, `auto_swing_batch.py:47` 의 `"NASD"` 키 → `is_overseas()` 기반 미국 거래시간 선택 (NYS/NAS/AMS 동일 시간)
6. **클라이언트 계약** — 라우터 `mrkt_code` Query 설명/Order schema 주석을 `NYS/NAS/AMS` 로 갱신 (응답 MRKT_CODE 는 STOCK_INFO 값 그대로)
7. **DB 마이그레이션 실행** — SWING_TRADE(및 필요 시 STOCK_DAY_HISTORY) `NASD → NAS`
8. **검증** — 실전/모의 계정으로 3거래소 종목 주문·잔고·시세·매핑 동작 확인

## 영향 범위
- 신규: `app/domain/stock/market_code.py`
- 수정: `foreign_api.py`, `order/service.py`, `order/entity.py`, `swing/{entity,service,repository}.py`, `swing/trading/{order_executor,auto_swing_batch}.py`, `stock/{repository,router,stock_data_batch}.py`
- DB: SWING_TRADE (필요 시 STOCK_DAY_HISTORY) 데이터 마이그레이션
- 스키마 변경 없음 (컬럼 값만 통일)

## 검증 기준
- [ ] NYS·NAS·AMS 각 거래소 종목에 대해 주문/정정취소/미체결 조회 성공 (OVRS_EXCG_CD=NYSE/NASD/AMEX)
- [ ] 시세/호가/순위 조회가 EXCD=정식코드로 정상 동작
- [ ] 실전 잔고: NASD(미국전체) 1회로 전 거래소 보유종목 조회
- [ ] 모의 잔고: 3거래소 순회 병합으로 전 거래소 보유종목 조회
- [ ] `mapping_swing` 이 SWING_TRADE↔STOCK_INFO 조인 정상 (MRKT_CODE 통일)
- [ ] 코드 내 잔존 `== "NASD"` / 하드코딩 `"NAS"`/`"NASD"` 없음 (매핑 경계 제외)
- [ ] SWING_TRADE 에 `MRKT_CODE='NASD'` 잔존 행 없음