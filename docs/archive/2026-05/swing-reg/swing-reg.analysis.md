# swing-reg Gap Analysis Report v2.0

> **Feature**: swing-reg (스윙 등록/수정 시 보유 자본 한도 검증)
> **Date**: 2026-05-08
> **Match Rate**: 95%
> **Status**: PASS
> **Previous**: v1.0 (2026-04-15, 100% — outdated design 기준)

---

## Overall Scores

| Category | Items | Match | Score | Status |
|----------|:-----:|:-----:|:-----:|:------:|
| 3.1 Capital Formula | 3 | 3 | 100% | PASS |
| 3.2 Repository | 7 | 7 | 100% | PASS |
| 3.3 Service | 18 | 17 | 94% | PASS |
| 3.4 Router | 6 | 6 | 100% | PASS |
| 3.5 Error Response | 5 | 4 | 80% | PASS |
| 5. API Spec | 5 | 4 | 80% | WARN |
| 8. Edge Cases | 4 | 4 | 100% | PASS |
| Architecture Compliance | 5 | 5 | 100% | PASS |
| Convention Compliance | 5 | 5 | 100% | PASS |
| **Overall** | **58** | **55** | **95%** | **PASS** |

---

## Checked Items (55/58)

### Capital Formula (3/3)
- `tot_evlu_amt` 사용 — 설계와 구현 일치 (service.py:49)
- `USE_YN = 'Y'` 필터로 활성 스윙만 할당 집계 (repository.py:135)
- 시장 분류 (NASD=해외, 그 외=국내) 정확

### Repository (7/7)
- `get_total_init_amount` 메서드명, 파라미터, 반환타입 일치
- `COALESCE(SUM, 0)` → `func.coalesce(func.sum(...), 0)` 정확
- `USE_YN == 'Y'` 필터 적용 (repository.py:135)
- overseas=True/False 분기 정확
- exclude_swing_id 조건 정확

### Service (17/18)
- `get_available_capital` 메서드 시그니처 및 로직 일치
- 국내/해외 API 분기 정확
- 총 자본: `tot_evlu_amt` 사용 일치
- `create_swing` — 자본 검증 미수행 (USE_YN='N') 설계와 일치
- `update_swing` — INIT_AMOUNT 변경 + USE_YN 활성화(N→Y) 두 트리거 일치
- Smart exclude_id (활성이면 자기 제외, 활성화 중이면 제외 불필요) 일치
- BusinessRuleError rule="CAPITAL_LIMIT_EXCEEDED" 일치
- detail 4개 키 일치

### Router (6/6)
- `GET /available-capital` 엔드포인트 존재
- Query 파라미터 (account_no 필수, mrkt_code 기본값 "J") 일치
- `/{swing_id}` 패턴 이전 배치 확인
- 의존성 주입 패턴 일관

### Error Response (4/5)
- HTTP 400, error_code: BUSINESS_RULE_VIOLATION 확인
- 금액 포맷팅 (`:,`) 적용 확인
- detail 4개 키 확인

### API Spec (4/5)
- GET/PUT 엔드포인트 설계대로 동작
- Response 형식 일치

---

## Gaps Found (3건 — 모두 Minor)

### 1. DOC-INCONSISTENCY: Section 5.2 vs 3.3.2 내부 모순

| 항목 | Section 3.3.2 | Section 5.2 |
|------|---------------|-------------|
| POST /swing 검증 | "자본 검증 불필요" | "추가 에러 응답 (400)" 기재 |

**영향**: Low — 구현은 3.3.2를 올바르게 따름
**권장**: Section 5.2에서 POST /swing 에러 케이스 제거 또는 "Section 3.3.2 참조" 주석 추가

### 2. MINOR: `rule` 파라미터 미사용

`BusinessRuleError(rule="CAPITAL_LIMIT_EXCEEDED", detail={...})` 호출 시 `detail`이 제공되면 `rule`은 무시됨 (domain.py:104의 `detail or {"rule": rule}` 로직).

**영향**: Very Low — 에러 응답에 rule 식별자 미포함
**권장**: Optional — detail에 `"rule"` 키 추가하거나 파라미터 제거

### 3. MINOR: `int()` 타입 캐스트

`data.get("INIT_AMOUNT", int(swing.INIT_AMOUNT))` — 설계 의사코드에는 없는 명시적 int 변환.

**영향**: None — 타입 일관성 개선 (Decimal vs int 비교 방지)
**권장**: 조치 불필요 (의도적 개선)

---

## Conclusion

설계 문서 v2.0과 구현 코드의 매칭률 95%. 모든 기능 요구사항이 정확히 구현됨.
3건의 Minor 차이는 기능에 영향 없음. Report 단계 진행 가능.

---

## Version History

| Version | Date | Match Rate | Changes |
|---------|------|:----------:|---------|
| 1.0 | 2026-04-15 | 100% | 초기 분석 (구 설계 기준) |
| 2.0 | 2026-05-08 | 95% | 설계 업데이트 후 재분석 (tot_evlu_amt, USE_YN 필터, create/update 전략) |
