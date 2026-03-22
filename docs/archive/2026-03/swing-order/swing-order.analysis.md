# swing-order Analysis Report

> **Analysis Type**: Gap Analysis (Plan vs Implementation)
>
> **Project**: AutoTrader
> **Analyst**: gap-detector
> **Date**: 2026-03-15
> **Plan Doc**: [swing-order.plan.md](../01-plan/features/swing-order.plan.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Plan 문서에서 정의된 "매도 실현손익 데이터 적재" 기능이 실제 구현 코드와 얼마나 일치하는지 확인한다.

Plan은 원래 6개 컬럼(COMMISSION, TAX, NET_PROCEEDS, REALIZED_PNL, REALIZED_PNL_PCT, AVG_BUY_PRICE)을 제안했으나, 사용자 피드백을 반영하여 2개 컬럼(TOTAL_FEE, REALIZED_PNL)으로 축소 정제되었다. 이 분석은 정제된 요구사항 기준으로 구현 일치율을 평가한다.

### 1.2 Analysis Scope

- **Plan Document**: `docs/01-plan/features/swing-order.plan.md`
- **Implementation Files**:
  - `app/domain/trade_history/entity.py`
  - `app/domain/trade_history/schemas.py`
  - `app/domain/trade_history/repository.py`
  - `app/domain/trade_history/service.py`
  - `app/domain/swing/trading/order_executor.py`

---

## 2. Plan vs Implementation: Column Design

### 2.1 Plan 원안 (6 Columns) vs 정제안 (2 Columns) vs 구현

| Plan 원안 Column | 정제 후 | 구현 상태 | 근거 |
|------------------|---------|-----------|------|
| COMMISSION | TOTAL_FEE로 합산 | TOTAL_FEE Column 존재 | entity.py:21 |
| TAX | TOTAL_FEE로 합산 | TOTAL_FEE Column 존재 | entity.py:21 |
| NET_PROCEEDS | 제외 (조회 시 계산) | 미구현 (의도적) | 정제 피드백 반영 |
| REALIZED_PNL | 유지 | REALIZED_PNL Column 존재 | entity.py:22 |
| REALIZED_PNL_PCT | 제외 (FE 계산) | 미구현 (의도적) | 정제 피드백 반영 |
| AVG_BUY_PRICE | 제외 (ENTRY_PRICE 조회) | 미구현 (의도적) | 정제 피드백 반영 |

**결론**: 정제된 2개 컬럼(TOTAL_FEE, REALIZED_PNL) 모두 구현 완료.

### 2.2 Entity 구현 검증

| 항목 | Plan 요구사항 | 구현 | Status |
|------|-------------|------|--------|
| TOTAL_FEE Column | DECIMAL, nullable | `Column(DECIMAL(15, 2), nullable=True)` | Match |
| REALIZED_PNL Column | DECIMAL, nullable | `Column(DECIMAL(15, 2), nullable=True)` | Match |
| comment 문서화 | - | comment 포함 ('제비용합계', '실현손익') | Match |
| 매수 시 NULL | nullable=True | nullable=True (매수 시 값 미전달) | Match |

---

## 3. Gap Analysis: Feature Implementation

### 3.1 구현 항목 비교

| # | Plan 요구사항 | 구현 파일 | 구현 위치 | Status |
|---|-------------|----------|----------|--------|
| 1 | Entity에 컬럼 추가 | entity.py | L21-22 | Match |
| 2 | Schema Response에 Optional 필드 추가 | schemas.py | L19-20 | Match |
| 3 | Repository save()에 새 필드 매핑 | repository.py | L39-40 | Match |
| 4 | Service에 매도 손익 계산 로직 | service.py | L99-142 (_calculate_sell_pnl) | Match |
| 5 | record_trade에서 매도 시 호출 | service.py | L79-82 | Match |
| 6 | COMMISSION_RATE, TAX_RATE 상수 정의 | service.py | L31-32 | Match |
| 7 | order_executor에서 매도 시 record_trade 호출 | order_executor.py | L192-198, L368-373 | Match |

### 3.2 손익 계산 로직 비교

| 항목 | Plan 수식 | 구현 코드 | Status |
|------|----------|----------|--------|
| 수수료율 | 0.00147 | `COMMISSION_RATE = Decimal("0.00147")` | Match |
| 세금률 | 0.0020 | `TAX_RATE = Decimal("0.0020")` | Match |
| 제비용 합산 | commission + tax | `sell_amount * (COMMISSION_RATE + TAX_RATE)` | Match |
| 실현손익 | (매도가-매수평균가)*수량 - 수수료 - 세금 | `(sell_price - avg_buy_price) * sell_qty - total_fee` | Match |
| 수익률 | ((매도가/매수평균가)-1)*100 | 미구현 (정제안에서 제외) | Match (의도적) |
| 매수평균가 출처 | SwingTrade.ENTRY_PRICE | `select(SwingTrade.ENTRY_PRICE).where(...)` | Match |

### 3.3 경계 조건 처리

| 경계 조건 | Plan 언급 | 구현 | Status |
|----------|----------|------|--------|
| entry_price 없는 경우 | 미명시 | `if not entry_price or entry_price <= 0: return {}` | Added (구현 우수) |
| 매수 시 새 컬럼 | NULL | trade_data에 미포함 -> Repository에서 `.get()` 으로 None | Match |
| sell_qty == 0 | 미명시 | `if trade_type == "S" and sell_qty > 0:` | Added (구현 우수) |

---

## 4. Architecture Compliance

### 4.1 Layer Dependency

| Layer | Expected | Actual | Status |
|-------|----------|--------|--------|
| Entity | 독립 (Base만 의존) | SQLAlchemy Base만 import | Match |
| Schema | Pydantic만 | pydantic, datetime, decimal, typing | Match |
| Repository | Entity, SQLAlchemy | entity, SwingTrade, Account import | Match |
| Service | Repository, Schema, Exception | repository, schemas, exceptions | Match |
| Router | Service, DI | service, dependencies, response | Match |

### 4.2 DDD Lite 패턴 준수

| 패턴 | 기대 | 실제 | Status |
|------|------|------|--------|
| Repository: flush only | `flush()` + `refresh()` | repository.py L44-45 | Match |
| Service: commit/rollback | commit은 호출자에서 | service.py L87 주석: "commit은 호출자(전략)에서 수행" | Match |
| DI: Depends factory | `get_{domain}_service` 패턴 | router.py L17-19: `get_trade_history_service` | Match |

### 4.3 아키텍처 위반 사항

| File | Issue | Severity |
|------|-------|----------|
| service.py L119-123 | `_calculate_sell_pnl` 내에서 직접 `select()` 쿼리 실행 (Repository 우회) | Minor |

**상세**: `_calculate_sell_pnl()` 메서드가 `SwingTrade.ENTRY_PRICE`를 직접 SQLAlchemy select로 조회한다. 엄격한 계층 분리에서는 Repository를 통해야 하나, 이는 단일 스칼라 조회이며 SwingRepository가 아닌 다른 도메인의 엔티티를 참조하므로 실용적 타협으로 볼 수 있다.

---

## 5. Convention Compliance

### 5.1 Naming Convention

| Category | Convention | Compliance | Violations |
|----------|-----------|:----------:|------------|
| Class | PascalCase | 100% | - |
| Function | snake_case | 100% | - |
| Constants | UPPER_SNAKE_CASE | 100% | COMMISSION_RATE, TAX_RATE |
| DB Column | UPPER_SNAKE_CASE | 100% | TOTAL_FEE, REALIZED_PNL |
| File | lowercase + underscore | 100% | - |

### 5.2 Import Order

| File | External first | Internal absolute | Relative | Status |
|------|:--------------:|:-----------------:|:--------:|--------|
| entity.py | sqlalchemy, datetime | app.common.database | - | Match |
| schemas.py | pydantic, datetime, decimal, typing | - | - | Match |
| repository.py | sqlalchemy, typing, datetime | app.domain.* | - | Match |
| service.py | sqlalchemy, typing, datetime, decimal, json, logging | app.domain.*, app.exceptions | - | Match |

### 5.3 Convention Score

```
Convention Compliance: 100%
  Naming:           100%
  Folder Structure: 100%
  Import Order:     100%
  Pattern (DI):     100%
```

---

## 6. Plan Document Update Needed

### 6.1 Plan 원안 -> 정제안 반영

Plan 문서(`swing-order.plan.md`)는 아직 6개 컬럼 원안을 기술하고 있다. 실제 구현은 사용자 피드백 기반 2개 컬럼 정제안을 따른다. Plan 문서 업데이트가 필요하다.

| Item | Current Plan | Actual Implementation | Action |
|------|-------------|----------------------|--------|
| Column count | 6 (COMMISSION, TAX, NET_PROCEEDS, REALIZED_PNL, REALIZED_PNL_PCT, AVG_BUY_PRICE) | 2 (TOTAL_FEE, REALIZED_PNL) | Plan 문서 업데이트 필요 |
| 구현 순서 Step 1 | "6개 컬럼 추가" | 2개 컬럼 추가 | Plan 문서 업데이트 필요 |

### 6.2 구현에서 추가된 항목 (Plan에 없음)

| Item | Implementation Location | Description |
|------|------------------------|-------------|
| entry_price 방어 로직 | service.py:127-129 | entry_price가 없거나 0 이하일 때 손익 계산 스킵 |
| sell_qty 방어 로직 | service.py:79 | sell_qty > 0 조건 체크 |
| 분할 매도 record_trade | order_executor.py:368-373 | 분할 매도 시에도 chunk별 record_trade 호출 |
| 분할 매수 record_trade | order_executor.py:312-318 | 분할 매수 시에도 chunk별 record_trade 호출 |

---

## 7. Overall Score

### 7.1 Match Rate Summary

```
Overall Match Rate: 95%

  Matched Items:           14 / 14 (100%)  - 정제안 기준 전체 구현 완료
  Missing in Implementation: 0 / 14 (0%)   - 미구현 항목 없음
  Added in Implementation:   4 items        - 방어 로직 추가 (긍정적)
  Plan Doc Outdated:         1 item         - 원안 6컬럼 -> 정제안 2컬럼 미반영
```

### 7.2 Category Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match (정제안 기준) | 100% | Match |
| Architecture Compliance | 95% | Minor: Service 직접 쿼리 1건 |
| Convention Compliance | 100% | Match |
| **Overall** | **95%** | Match |

---

## 8. Recommended Actions

### 8.1 Documentation Update (Low Priority)

| # | Item | Description |
|---|------|-------------|
| 1 | Plan 문서 업데이트 | 6개 컬럼 원안을 2개 컬럼 정제안으로 수정, 정제 사유 기록 |

### 8.2 Optional Architecture Improvement (Backlog)

| # | Item | File | Description |
|---|------|------|-------------|
| 1 | _calculate_sell_pnl 쿼리 분리 | service.py:119-123 | SwingRepository에 `find_entry_price(swing_id)` 추가 후 호출. 단, 현재 구조에서 cross-domain 접근이므로 실용적 판단 필요 |

---

## 9. Conclusion

정제된 요구사항(2개 컬럼: TOTAL_FEE, REALIZED_PNL) 기준으로 **모든 기능 항목이 100% 구현**되었다. Entity, Schema, Repository, Service, OrderExecutor 전 계층에 걸쳐 일관되게 반영되었으며, Plan에 없던 방어 로직(entry_price null 체크, sell_qty 체크)이 추가되어 구현 품질이 Plan보다 우수하다.

유일한 개선 포인트는 Plan 문서 자체가 원안(6컬럼)을 그대로 담고 있어 정제 결과를 반영하는 업데이트가 필요하다는 점이다.

**Match Rate: 95% -- Check phase 통과 (>= 90%)**

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-15 | Initial analysis | gap-detector |
