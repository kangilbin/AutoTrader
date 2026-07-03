# Plan: 해외증거금 통화별조회 기반 미국장 잔고/가용자본 산출

## 개요
미국장(NASD) 잔고 조회에서 현금/주문가능금액 산출의 소스를 **해외증거금 통화별조회 [해외주식-035] (TTTC2101R)** 로 교체한다. 평가금액·평가손익은 기존 해외 주식 잔고 API(`get_stock_balance`, TTTS3012R) 보유종목을 합산해 계산한다.

## 문제 정의

### 현재 구조
미국장 현금/가용자본은 `get_present_balance`(CTRP6504R, `inquire-present-balance`)에 의존한다.

| 사용처 | 쓰는 값 | 용도 |
|--------|---------|------|
| `get_available_capital` (service.py:45) | `dnca_tot_amt`(외화예수금) → `cash` | `available_capital = cash − allocated` |
| `mapping_swing` (service.py:221) | `output2` 전체 | summary: `CASH_ASSET`, `TOTAL_INVESTMENT_AMOUNT`(총평가), `TOTAL_PROFIT`(손익) 등 |

### 문제점
1. **가용자본 부정확**: `dnca_tot_amt`(외화예수금)는 실제 "주문 가능" 금액이 아니다. 미결제·증거금 사용분이 반영된 **주문가능금액**과 다르다.
2. **현금 표시와 주문가능 개념 혼용**: "내 현금 자산 표시"와 "매수 가능 금액"은 다른 값인데 같은 필드(`dnca_tot_amt`)를 써왔다.

## 변경 사항

### 핵심 매핑 (확정)

| 표시/계산 항목 | 새 소스 (035, TTTC2101R) | 비고 |
|---------------|--------------------------|------|
| 가용자본 (`/available-capital`의 `total_capital`) | **외화주문가능금액** | 실제 주문 가능 금액 |
| 현금 자산 표시 (`mapping_swing` summary `CASH_ASSET`) | **외화예수금** (`frcr_dncl_amt1`) | 보유 현금 |
| 평가금액 (`TOTAL_INVESTMENT_AMOUNT`) | `get_stock_balance` 보유종목 `evlu_amt` 합산 + 현금 | 035에 평가금액 없음 |
| 평가손익 (`TOTAL_PROFIT`) | `get_stock_balance` 보유종목 `evlu_pfls_amt` 합산 | 035에 평가손익 없음 |

> **검증 필요 (구현 중 실 응답으로 확인)**: "외화주문가능금액"에 해당하는 정확한 필드.
> 명세상 후보 — `frcr_ord_psbl_amt1`(한글명 "외화주문가능금액", 단 Description은 "원화주문가능환산금액"으로 상충),
> `frcr_gnrl_ord_psbl_amt`(외화일반주문가능금액, 순수 외화). 실 응답값을 보고 USD 기준으로 확정한다.

### 모의투자 처리
- 사용자 결정: **모의/실전 모두 본 API(TTTC2101R)로 잔고 조회**한다. 별도 fallback 분기 없음.
- ⚠️ **리스크**: KIS 명세상 035는 "모의투자 미지원"이다. 모의 계정에서 호출 시 실패할 가능성이 있으며, 이는 **테스트 단계에서 실제 모의 계정으로 검증**해 확인한다. 실패 시 대응(에러 처리 vs fallback 부활)은 그때 결정한다.

### get_present_balance 처리
- 교체 후 `get_present_balance`(CTRP6504R)는 호출처가 사라진다(현재 2곳이 유일 사용처).
- 데드코드 방지를 위해 **제거**하되, 변경 diff에서 제거 사유를 함께 설명하고 accept 받는다.

## 구현 순서

1. **`app/external/foreign_api.py`** — 신규 `get_foreign_margin(user_id, db)` 추가
   - GET `uapi/overseas-stock/v1/trading/foreign-margin`, TR_ID `TTTC2101R`
   - Query: `CANO`, `ACNT_PRDT_CD`
   - output에서 USD(`crcy_cd == "USD"`) row 추출 → `{외화예수금, 외화주문가능금액, 기준환율 ...}` 정규화 반환
2. **`app/domain/swing/service.py` `get_available_capital`** — overseas 분기에서 `get_present_balance` → `get_foreign_margin` 교체, `total_capital`을 **외화주문가능금액**으로
3. **`app/domain/swing/service.py` `mapping_swing`** — overseas 분기:
   - 현금/주문가능: `get_foreign_margin`
   - summary 평가/손익: `get_stock_balance` 보유종목 합산으로 계산 (`tot_evlu_amt`, `evlu_pfls_smtl_amt` 대체)
   - `CASH_ASSET` = 외화예수금
4. **`get_present_balance` 제거** (호출처 정리 후)
5. **검증**: 실 계정(실전/모의)으로 `/available-capital`, `/swing/list` 호출하여 값/동작 확인

## 영향 범위
- `app/external/foreign_api.py` (신규 함수 + 기존 함수 제거)
- `app/domain/swing/service.py` (`get_available_capital`, `mapping_swing`)
- 도메인/엔티티/DB 스키마 변경 **없음**

## 검증 기준
- [ ] 미국장 `/available-capital`이 주문가능금액 기반으로 `total_capital` 반환
- [ ] `/swing/list` summary의 `CASH_ASSET`=외화예수금, `TOTAL_INVESTMENT_AMOUNT`/`TOTAL_PROFIT`이 보유종목 합산과 일치
- [ ] 모의 계정에서의 동작 확인 (실패 여부 및 대응 결정)
- [ ] `get_present_balance` 잔존 참조 없음
