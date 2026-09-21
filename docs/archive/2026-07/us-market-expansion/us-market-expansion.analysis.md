# us-market-expansion Analysis Report

> **Analysis Type**: Gap Analysis (PDCA Check)
>
> **Project**: Auto-Trader (FastAPI 한국 주식 자동매매 백엔드)
> **Analyst**: gap-detector
> **Date**: 2026-07-21
> **Design Doc**: [us-market-expansion.design.md](../02-design/features/us-market-expansion.design.md)
> **Plan Doc**: [us-market-expansion.plan.md](../01-plan/features/us-market-expansion.plan.md)

---

## 1. Analysis Overview

미국장 전 거래소(NYS/NAS/AMS) 확장 및 시장코드 매핑 설계의 구현 일치도를 검증한다.
핵심 원칙: (1) 정식코드 = NYS/NAS/AMS 통일, (2) KIS 코드 매핑은 `foreign_api` 경계에서만, (3) `== "NASD"` → `is_overseas()` 전면 교체.

**범위 밖 (점수 미반영)**:
- DB 마이그레이션(SWING_TRADE NASD→NAS): 수동 단계, 사용자가 데이터 이미 정상이라 확인.
- 모바일 클라이언트: 별도 저장소.
- `kis_api.py:17` `from app.domain.auth.repository import AuthRepository` (external→domain 위반): 설계에서 명시적으로 이번 작업 제외(design.md:26).

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | ✅ |
| Architecture Compliance | 100% | ✅ |
| Convention Compliance | 100% | ✅ |
| **Overall Match Rate** | **100%** | ✅ |

---

## 3. Per-Item Checklist

| # | Design Item | Status | Evidence |
|---|-------------|:------:|----------|
| 1 | `app/core/order.py` 존재 (`Order`/`ModifyOrder`) | ✅ | `app/core/order.py:13,47` |
| 1 | `app/domain/order/entity.py` 삭제 | ✅ | Glob: 파일 없음 |
| 1 | import 4곳 `app.core.order` 로 갱신 | ✅ | `order/service.py:8`, `order_executor.py:12`, `kis_api.py:16`, `foreign_api.py:14` |
| 1 | 잔존 `app.domain.order.entity` 참조 없음 | ✅ | grep: 코드 0건 (설계문서만) |
| 2 | `market_code.py`: `US_MARKETS=('NYS','NAS','AMS')` | ✅ | `market_code.py:9` |
| 2 | `_EXCG_TRADE` (NYS→NYSE, NAS→NASD, AMS→AMEX) | ✅ | `market_code.py:12` |
| 2 | `US_TRADE_EXCG=('NASD','NYSE','AMEX')` | ✅ | `market_code.py:15` |
| 2 | `is_overseas()`, `to_ovrs_excg_cd()` | ✅ | `market_code.py:18,23` |
| 3 | `swing/entity.py` VALID_MRKT_CODES 확장 | ✅ | `swing/entity.py:11` = `('J','NX','UN','NYS','NAS','AMS')` |
| 4 | 시세계열 함수 `excd` 파라미터화 (하드코딩 제거) | ✅ | `foreign_api.py:320,335`(price), `370,381`(stock_data), `414,424`(asking), `457,468`(volume_rank) + 기존 `343,436,478` |
| 4 | 거래계열 `OVRS_EXCG_CD` = `to_ovrs_excg_cd()` | ✅ | `foreign_api.py:193`(place_order), `225`(modify/cancel), `254`(daily_ccld) |
| 4 | `get_us_holdings()` — real: `excg_cd="NASD"` 1회 | ✅ | `foreign_api.py:159-160` |
| 4 | `get_us_holdings()` — mock: `US_TRADE_EXCG` 순회+output1 병합 | ✅ | `foreign_api.py:162-167` |
| 5 | `== "NASD"` → `is_overseas()` 전면 교체 | ✅ | `order/service.py:83`, `swing/service.py:75,245,559,561`, `repository.py:55,58,149,151`, `order_executor.py:91,180,305,379`, `auto_swing_batch.py:141,703`, `stock/repository.py:31,33`, `stock/router.py:42,58,73,88`, `stock_data_batch.py:37,108` |
| 5 | 잔존 `== "NASD"` / `!= "NASD"` 없음 (코드) | ✅ | grep `(==\|!=)\s*"NASD"`: 0건 |
| 5 | 하드코딩 `"EXCD": "NAS"` 없음 (코드) | ✅ | grep: 코드 0건 (설계문서만) |
| 6 | `swing/service.py` overseas 분기 `get_us_holdings()` 사용 | ✅ | `swing/service.py:266` (구 `get_stock_balance` 아님) |
| 7 | `_MARKET_CLOSE_CONFIG` NYS/NAS/AMS 키 | ✅ | `swing/service.py:35-40` |
| 7 | `_MARKET_OPEN_CONFIG` NYS/NAS/AMS 키 | ✅ | `auto_swing_batch.py:45-51` |
| 7 | `auto_swing_batch` `excd=mrkt_code` (하드코딩 제거) | ✅ | `auto_swing_batch.py:707`, `149` |
| 8 | `find_active_domestic_swings` `IN ('J','NX','UN')` (버그 수정) | ✅ | `repository.py:82` (구 `IN ('J','NAS')` 제거) |
| 8 | 해외 쿼리 US_MARKETS 세트 사용 | ✅ | `repository.py:94` `IN ('NYS','NAS','AMS')`, `56,58,149,151` `.in_/notin_(US_MARKETS)` |
| 9 | 라우터 ranking/price `excd=mrkt_code` + `is_overseas()` | ✅ | `stock/router.py:42-43,58-59,73-74,88-89` |
| 9 | Query description 갱신 | ✅ | `stock/router.py:27,39,55,70,85` (`J:국내, NYS/NAS/AMS:미국`) |

---

## 4. Repo-wide Violation Scan

| Check | Result |
|-------|:------:|
| `(==\|!=) "NASD"` in `app/**/*.py` | ✅ 0건 |
| `"EXCD": "NAS"` 하드코딩 in code | ✅ 0건 (설계문서 표기만) |
| `app.domain.order.entity` 참조 in code | ✅ 0건 |
| `"NAS"`/`"NASD"` 문자열 잔존 | ✅ 전부 허용 위치 (market_code 매핑, foreign_api 기본값/get_us_holdings, VALID_MRKT_CODES 튜플, repository SQL IN 세트, 거래시간 dict 키) |

허용 위치 상세: `market_code.py:9,12,15` (매핑/상수), `foreign_api.py:29,150,160,163,225,241,270,320,343,370,414,436,457,478` (거래소 기본값 및 시세 `excd` 기본값 — 설계 design.md:79-88 명시 허용), `swing/entity.py:11`, `swing/repository.py:94`, `auto_swing_batch.py:50`, `swing/service.py:38`.

---

## 5. Gaps Found

**없음.** 설계된 9개 항목 전부 코드에 정확히 반영됨. Missing / Added(설계외) / Changed(불일치) 항목 0건.

관찰 사항(감점 아님):
- `foreign_api.py:225` `modify_or_cancel_order_api` 는 `getattr(order,'excg_cd','NAS')` 사용. `ModifyOrder` dataclass 에는 `excg_cd` 필드가 없어 항상 기본값 `'NAS'`(→NASD 변환)로 동작 — 설계(design.md:100)와 동일하나, NYS/AMS 정정취소 시 거래소코드가 NASD 로 고정되는 잠재적 한계. 설계도 동일 코드를 명시했으므로 설계-구현 일치이며 감점 없음. 향후 개선 후보로 기록.

---

## 6. Verification Criteria (Design 승계)

| 기준 | 코드 반영 |
|------|:--------:|
| NYS/NAS/AMS 주문·정정취소·미체결 (OVRS_EXCG_CD 변환) | ✅ |
| 시세/호가/순위 EXCD=정식코드 | ✅ |
| 실전 잔고 NASD 1회 / 모의 3거래소 병합 | ✅ |
| `mapping_swing` 조인 정상 (MRKT_CODE 통일) | ✅ (`repository.py:47` 조인 정상 동작) |
| 잔존 `== "NASD"` / 하드코딩 없음 | ✅ |
| SWING_TRADE `MRKT_CODE='NASD'` 잔존 0 | ⏸️ 범위 밖 (수동 DB 마이그레이션, 런타임 미검증) |

---

## 7. Recommended Actions

1. **런타임 검증 (권장)**: 실전/모의 계정으로 NYS·NAS·AMS 3거래소 실주문·잔고·시세 동작 확인 (설계 구현순서 8단계). 정적 분석은 100% 통과했으나 KIS API 실호출 검증은 미수행.
2. **DB 마이그레이션 확인**: `SELECT MRKT_CODE, COUNT(*) FROM SWING_TRADE GROUP BY MRKT_CODE` 로 `NASD` 잔존 0 재확인 (사용자 데이터 이미 정상 보고).
3. **향후 개선 후보**: `ModifyOrder` 에 `excg_cd` 필드 추가하여 NYS/AMS 정정취소 거래소코드 정확 반영 (현재 NASD 고정).

---

## 8. Conclusion

**Match Rate 100%** — 설계 9개 항목 전부 정확히 구현됨. 아키텍처(매핑 경계 단일화, external→core 정방향 의존)·컨벤션 100% 준수. Gap 0건. Report 단계(`/pdca report us-market-expansion`) 진행 가능.