# Gap Analysis: 해외증거금 통화별조회 기반 미국장 잔고/가용자본 산출

- **기준 문서**: `docs/01-plan/features/foreign-margin-balance.plan.md` (설계 단계 생략, plan 기준)
- **분석 방식**: bkit:gap-detector 독립 검증
- **Match Rate**: **98%** (초기 96% → 모의투자 처리 구현 반영)

## 결론
Plan의 4대 핵심 매핑이 코드에 정확히 반영됨. 구현 순서 1~4단계 완료, `get_present_balance` 잔존 참조 0(grep 확인). 모의투자 미지원 리스크(초기 Gap #1)는 후속 구현으로 **해결**됨. 남은 미달 2%는 **주문가능 필드 단위 미확정**(실 응답 1회 확인 필요) 단일 항목.

## 정합 확인 (구현 OK)
| Plan 매핑 | 구현 | 상태 |
|-----------|------|------|
| 가용자본 = 외화주문가능금액 | `service.py` get_available_capital → `ord_psbl_amt` | ✅ |
| 현금 표시 = 외화예수금 | `mapping_swing` → `dnca_amt` → CASH_ASSET | ✅ |
| 평가금액 = 보유종목 evlu_amt 합 + 현금 | overseas output2 `tot_evlu_amt` 조립 | ✅ |
| 평가손익 = 보유종목 evlu_pfls_amt 합 | overseas output2 `evlu_pfls_smtl_amt` | ✅ |
| summary 정합 | `principal = tot_evlu − pfls` 에서 "현금 포함 총평가" 의미 보존 | ✅ |
| get_present_balance 제거 | 잔존 참조 0 | ✅ |

## 모의투자 처리 (결정 및 구현 — Gap #1 해결)

**도메인 사실**: 모의투자 계정은 외화예수금/주문가능금액 데이터 소스 자체가 없다(어떤 API든). 보유종목·종목별 수익률만 조회 가능.

**결정 (사용자 확정)**: "한도 검증 생략 + 미지원 표시". 모의는 가용자금 게이팅 없이 보유종목/수익률만 표시.

**구현**:
| 위치 | 모의(simulation_yn="Y") 동작 |
|------|------------------------------|
| `foreign_api.get_foreign_margin` | `None` 반환 (035 미지원을 API 함수가 책임) |
| `service.get_available_capital` | `total_capital`/`available_capital`=`None`, `capital_tracking:False` |
| `service.update_swing` 한도검증 | `available_capital is None`이면 검증 생략 (등록·활성화 자유) |
| `service.mapping_swing` | 평가/손익은 보유종목으로 표시, `CASH_ASSET`=`None`(미지원), 총평가는 현금 0 |

> 프론트엔드는 `capital_tracking:false` / `CASH_ASSET:null`을 "모의 미지원"으로 표기해야 함.

## Gap 목록

### Medium (실 계정 검증 필요)
1. **외화주문가능금액 필드 단위 미확정** (`foreign_api.py` `frcr_ord_psbl_amt1`)
   - 명세 한글명="외화주문가능금액" vs 설명="원화주문가능환산금액" 상충.
   - **만약 원화 환산값이면** USD 기준 가용자본이 ~환율배(약 1300배) 부풀려져 **자본 한도 검증이 무력화**되는 심각 결과.
   - 조치: 실 응답값 확인. 원화면 `gnrl_ord_psbl_amt`(외화일반주문가능금액)로 교체(이미 동시 반환 중 → 한 줄 수정).

### Low
3. **USD 센트 절삭** (`service.py` get_available_capital) — `int(float(...))`로 가용자본 소수점 버림. 기존 동작과 동일(의도된 유지)이나 USD는 센트 손실. 영향 경미.
4. **빈 currency_row 무음 0 반환** (`foreign_api.py`) — USD row 없으면 모든 값 "0" 반환. API 실패와 잔고 0 구분 불가. 로깅 고려.

## 미수행 (Plan 검증 기준 L63-66)
- [ ] 실전 계정 `/available-capital`·`/swing/list` 호출 검증 (구현 순서 5단계)
- [x] 모의 계정 동작 결정·구현 (한도 생략 + 미지원 표시) — 실 모의 계정 호출 확인은 잔여

## 권고
Match Rate 98% ≥ 90%이므로 report 단계 진입 가능. 단, **Gap #1(주문가능 필드 단위)은 자본 한도 직결**이라 report 전 실전 계정 실 응답 1회 확인을 강력 권장.
