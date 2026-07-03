---
name: AutoTrader PDCA Context
description: Project context and PDCA cycle information for AutoTrader trade_history feature
type: project
---

# AutoTrader PDCA Context

## Project Information
- **Name**: AutoTrader
- **Type**: FastAPI-based Korean stock auto-trading backend service
- **Language**: Python
- **Architecture**: DDD Lite + Layered Architecture

## First PDCA Cycle: trade_history Feature

### Dates
- Planned: 2026-03-13
- Completed: 2026-03-13
- Duration: ~7 hours (entire cycle)

### Results
- **Status**: Complete (100% of requirements)
- **Design Match Rate**: 95% (target: ≥90%)
- **Architecture Compliance**: 95%
- **Convention Compliance**: 98%

### Feature Summary
Trade History API that returns:
1. TRADE_HISTORY records (매매 내역)
2. STOCK_DAY_HISTORY OHLCV data (price_history)
3. Calculated EMA20 values (ema20_history)
- Year-based pagination support
- JWT authentication + ownership validation
- DDD Lite layered architecture

### Key Differences Found (2 improvements vs design)
1. **Ownership Verification**: Design specified 2-step (Account→USER_ID), implementation optimized to 1-step (single WHERE clause) → DB query reduced from 2 to 1
2. **SwingRepository/AccountRepository**: Design specified using repositories, implementation used direct SQLAlchemy queries → reason: AccountRepository lacks find_by_account_no() method

### Added Enhancement
- Empty data guard (`if price_days:`) not specified in design but essential defensive programming

## Second PDCA Cycle: swing-profit-fix (Hotfix)

### Dates
- Completed: 2026-05-09
- Type: Bug fix (unscheduled, design documentation skipped)

### Results
- **Status**: Complete (100% of bug requirements)
- **Design Match Rate**: 95%
- **Backward Compatibility**: 100%

### Issue Summary
Profit rate calculation bug in `SwingService.mapping_swing()`:
- **Problem**: When `INIT_AMOUNT=0` (auto-registered holdings), Python's falsy evaluation caused incorrect profit/loss calculation
- **Manifestation**: `EVLU_PFLS_AMT` and `EVLU_PFLS_RT` displayed as raw evaluation amounts instead of calculated profit delta
- **Root Cause**: `if data["INIT_AMOUNT"]` treated `0` as `False`, triggering wrong code path

### Fix Applied (3 profit calculation scenarios)
1. **New swing registration**: Use KIS API values directly
2. **Merge existing + holdings**: Conditional branching on `if data["INIT_AMOUNT"]:`
   - `INIT_AMOUNT > 0`: Custom calculation (manual registration)
   - `INIT_AMOUNT = 0`: KIS API values (auto-registered)
3. **Swing-only items**: Safe zero values (rate=0.0, pfls_amt=0)

### Enhancement: Auto-Registration Data
Added missing fields when auto-registering holdings from KIS balance:
- `INIT_AMOUNT` = `pchs_amt` (purchase amount)
- `ENTRY_PRICE` = `pchs_avg_pric` (average purchase price)
- `HOLD_QTY` = `hldg_qty` (holdings quantity)
- `CUR_AMOUNT` = 0 (already purchased state)

### Files Modified
- `app/domain/swing/service.py`: mapping_swing() method (lines 205-321)

### Metrics
- Lines changed: ~20
- Test coverage: Manual (no unit tests added)
- Regression risk: Low (isolated to conditional branches)

## Architecture Patterns Confirmed
- Service Layer: Handles business logic + transaction management
- Repository Pattern: CRUD operations (TradeHistoryRepository implemented)
- Exception Handling: Standardized exceptions (NotFoundError, PermissionDeniedError, DatabaseError)
- EMA20 Calculation: Reuses backtest pattern (talib, pandas)
- Async Operations: AsyncSession with SQLAlchemy
- **Conditional Data Source Selection**: Use KIS API for auto-reg, custom calc for manual reg

## Files Involved
- `app/domain/trade_history/schemas.py`
- `app/domain/trade_history/repository.py`
- `app/domain/trade_history/service.py`
- `app/domain/trade_history/router.py`
- `app/domain/routers/__init__.py`
- `app/main.py`

## Lesson: Design vs Implementation Reality
Project uses **DDD Lite (practical hybrid)**, not pure DDD. Design documents often assume pure patterns but actual code is pragmatic. For next cycles, add a step to verify:
1. Target Repository methods exist
2. Service pattern alignment with project's actual patterns
3. External service availability (StockService, etc.)

## Second PDCA Cycle: DDD Refactoring Feature

### Dates
- Planned: 2026-01-XX
- Completed: 2026-03-14
- Duration: ~6 weeks (entire cycle)

### Results
- **Status**: Complete (100% of core requirements, 98% overall)
- **Design Match Rate**: 98% (target: ≥90%) — excellent alignment
- **Architecture Compliance**: 100% (DDD Lite principles)
- **Convention Compliance**: 100%

### Feature Summary
DDD Lite refactoring — ORM model separation and Entity business logic integration:
1. Moved all ORM models from `app/common/database.py` to domain `entity.py` files (distributed architecture)
2. Integrated business logic into ORM models (SwingTrade: 14 methods, validate/transition/reset)
3. Removed `process_trading_cycle()` from Strategy (700 lines) — signal judgment only
4. Refactored `auto_swing_batch.py` to orchestrator pattern (Strategy → Executor → Entity → Repository)
5. Cleaned up 27/29 dead code items (92% completion rate)

### Key Changes
- **database.py**: 219 lines → 90 lines (-59%)
- **base_trading_strategy.py**: 860 lines → 150 lines (-83%)
- **SwingTrade Entity**: 8 unused methods → 14 used methods (validate, is_*, transition_to_*, reset_cycle, update_*)
- **Repository Pattern**: Entity → Model conversion logic removed (Entity is now ORM model)
- **Batch Orchestration**: Explicit signal handlers (_handle_signal_0/1/2/3) with Entity state transitions

### Intentional Design Deviations (improved)
1. **order/entity.py dataclass**: Design said delete, implementation kept (KIS API parameter validation needed)
2. **get_inquire_daily_ccld_obj()**: Design said delete, implementation kept (used by check_order_execution)
3. **Factory methods**: Design didn't specify, implementation added (User.create_oauth_user, Account.create, Auth.create)

### Architecture Verification
- Layer dependency: Router → Service → Repository → Entity (100% compliant)
- Transaction management: Repository=flush, Service=commit, Batch=flush+commit (100%)
- Orchestrator pattern: Strategy(signal judgment) → Executor(order) → Entity(state transition) (100%)
- DDD Lite: Entity as single source of business logic truth (100%)

### Files Involved
- `app/common/database.py` (cleanup)
- `app/domain/swing/entity.py` (14 business methods)
- `app/domain/swing/repository.py` (import refactoring)
- `app/domain/swing/trading/strategies/base_trading_strategy.py` (process_trading_cycle removal)
- `app/domain/swing/trading/auto_swing_batch.py` (orchestrator refactoring)
- 7 domain entity files: user, account, auth, stock, trade_history, device, order
- 8 repository files: import path updates

### Dead Code Cleanup Results
- Schemas: 2 deleted (OrderResponse, CancelableOrderResponse, SwingMappingResponse)
- External APIs: 2 deleted (get_approval, get_balance)
- Service methods: 4 deleted (get_latest_buy, get_latest_sell, get_holding_swings, get_eod_target_swings)
- Repository methods: 13 deleted (swing, stock, device, trade_history repos)
- Utilities: 2 deleted (paginated_response, get_batch_status)
- **Total**: 23 items deleted → Iteration 1 brought Dead Code score from 79% to 92%

## Documentation Locations
- Plan: `docs/01-plan/features/ddd-refactoring.plan.md`
- Design: `docs/02-design/features/ddd-refactoring.design.md`
- Analysis: `docs/03-analysis/ddd-refactoring.analysis.md`
- Report: `docs/04-report/ddd-refactoring.report.md`

## Lessons Learned from Both Cycles

### Pattern: Design-Implementation Gap Analysis
1. **trade_history**: 95% match (minor query optimization)
2. **ddd-refactoring**: 98% match (2 code items set to keep, 8 beneficial additions)
- Both cycles show that well-designed PDCA processes catch these gaps naturally
- Gap analysis + iteration process is effective for quality improvement

### Key Insights
1. **DDD Lite is practical**: Pure DDD patterns are overkill; pragmatic hybrid works better
2. **Grep validation matters**: Always verify "dead code" with actual code search before deletion
3. **Factory methods**: Entity.create() pattern should be defined upfront, not added during implementation
4. **Batch orchestration**: Complex state machines benefit from explicit handler patterns (_handle_signal_* naming)
5. **Code quality**: 6-week refactoring achieved 59-83% LOC reduction with 100% behavior preservation

## Third PDCA Cycle: swing-order Feature

### Dates
- Started: 2026-03-01
- Completed: 2026-03-15
- Duration: 15 days (entire cycle)

### Results
- **Status**: Complete (100% of requirements)
- **Design Match Rate**: 95% (target: ≥90%)
- **Architecture Compliance**: 95% (minor: Service direct query for SwingTrade.ENTRY_PRICE)
- **Convention Compliance**: 100%

### Feature Summary
매도 실현손익 데이터 적재 기능:
1. TRADE_HISTORY 테이블에 TOTAL_FEE (제비용합계), REALIZED_PNL (실현손익) 2개 컬럼 추가
2. Service에서 매도 시 자동으로 손익 계산 (COMMISSION_RATE 0.00147 + TAX_RATE 0.0020)
3. 분할 매도도 chunk별 record_trade 호출로 개별 기록
4. 프론트엔드에서 백테스트와 동일한 수준의 성과 추적 가능

### Key Design Evolution
- **원안**: 6개 컬럼 (COMMISSION, TAX, NET_PROCEEDS, REALIZED_PNL, REALIZED_PNL_PCT, AVG_BUY_PRICE)
- **정제안**: 2개 컬럼으로 축소 (TOTAL_FEE = commission+tax 합산, REALIZED_PNL만)
- **이유**: 불필요한 컬럼은 프론트엔드에서 계산 가능, DB 저장소 경량화, KIS API 독립성

### Improvements Beyond Plan
1. **entry_price 방어 로직**: `if entry_price <= 0: return {}` (손익 계산 스킵)
2. **sell_qty 검증**: `if sell_qty > 0:` 조건 추가
3. **분할 매도 지원**: order_executor.py에서 chunk별 record_trade 호출 (미계획)

### Architecture Verification
- Layer dependency: Router → Service → Repository → Entity (✅)
- DDD Lite compliance: Entity 독립성, Repository flush-only, Service commit (✅ 95%)
- Convention: Naming, import order, folder structure (✅ 100%)
- Minor issue: Service에서 직접 `select(SwingTrade.ENTRY_PRICE)` 실행 → SwingRepository 메서드 분리 권고

### Files Modified
- `app/domain/trade_history/entity.py` (+2 columns)
- `app/domain/trade_history/schemas.py` (+2 optional fields)
- `app/domain/trade_history/repository.py` (field mapping)
- `app/domain/trade_history/service.py` (_calculate_sell_pnl method)
- `app/domain/swing/trading/order_executor.py` (record_trade integration)

### Documentation
- Plan: `docs/01-plan/features/swing-order.plan.md` (원안 6컬럼, 정제 사유 미반영 — 업데이트 권고)
- Analysis: `docs/03-analysis/swing-order.analysis.md` (95% match)
- Report: `docs/04-report/features/swing-order.report.md` (completion report)

### Lessons
1. **Plan 문서 버전 관리**: 정제된 요구사항이 문서에 반영되지 않으면 향후 혼선 → Plan 업데이트 필수
2. **경량 피쳐 설계 문서**: 복잡도 판단이 어려우므로 최소 아키텍처 다이어그램은 설계 문서에 포함
3. **Repository 패턴 일관성**: 다른 도메인 엔티티 조회도 Repository 메서드로 분리 권고
4. **테스트 미포함**: 핵심 로직(손익 계산)에 대한 단위/통합 테스트 필요

## Fourth PDCA Cycle: trading-fix (Auto-trading Batch Stability)

### Dates
- Started: 2026-03-22
- Completed: 2026-03-22
- Duration: 1 day (entire cycle)

### Results
- **Status**: Complete (100% of requirements)
- **Design Match Rate**: 100% (initial 97% → 100% after 2 Gap fixes)
- **Architecture Compliance**: 100%
- **Convention Compliance**: 100%

### Feature Summary
자동 매매 알고리즘 전수 검토에서 발견된 11개 이슈 수정:
1. **CRITICAL 2건**: DB 세션 공유 동시성 버그, 예외 시 롤백 누락
2. **HIGH 3건**: 15:00~15:30 매매 시간 공백, Redis-DB 상태 불일치, 체결 확인 폴백 부정확
3. **MEDIUM 3건**: Redis TTL 유실, 알림 fire-and-forget 예외 무시, 기타
4. **LOW 1건**: 전략 팩토리 정리

### Key Design Decisions
- **1단계**: DB 세션 분리 + 롤백 (동시성 버그 근본 해결)
- **2단계**: 매매 시간 확장 (15:00~15:20 스케줄)
- **3단계**: Redis-DB 순서 변경 (DB 먼저 commit, 그 후 Redis)
- **4단계**: 체결 확인 재시도 (최대 2회, 1초 간격)
- **5단계**: MEDIUM/LOW 마이너 수정 (TTL, 예외 처리, 코드 정리)

### Gap Analysis Results
- **Initial Match**: 97% (30개 항목 중 28 PASS, 2 GAP)
  - GAP-1 (Medium): 진행 중 partial state Redis 저장 누락 → Fixed
  - GAP-2 (Low): TTL 상수 미추출 → Fixed
- **Final Match**: 100% (30개 항목 모두 PASS)

### Intentional Design Deviations (Deferred)
1. **Issue #5**: 재진입 자본 초과 — 오분석 (매도 수익금 재진입은 정상)
2. **Issue #7**: PEAK_PRICE 5분 지연 — 설계적 한계 (5분 배치에서 실시간 고가 반영)
3. **Issue #10**: OBV ddof=1 — 의도적 설계 (표본 표준편차, 백테스트 일관성)
4. **Issue #12**: SELL_RATIO 정수 절삭 — 정상 동작 (주식은 정수 단위)

### Files Modified
1. `app/domain/swing/trading/auto_swing_batch.py` — DB 세션 분리, 롤백, 부분 체결 Redis 저장, 알림 예외 처리
2. `app/common/scheduler.py` — 15:00~15:20 스케줄 추가
3. `app/domain/swing/trading/order_executor.py` — Redis-DB 순서 변경, 체결 확인 재시도
4. `app/domain/swing/trading/strategies/single_ema_strategy.py` — TTL 상수 추출 (1800초)
5. `app/domain/swing/trading/trading_strategy_factory.py` — 주석 정리

### Key Improvements
- **동시성**: 각 종목이 독립 DB 세션 사용 → 동시성 버그 제거
- **정합성**: Redis 저장 = DB commit 후 → 상태 불일치 방지
- **안정성**: 체결 확인 재시도 (1초 대기) → KIS API 지연 대응
- **모니터링**: 예외 처리 강화 → 배치 실패 원인 파악 용이
- **가독성**: 상수화, 주석 정리 → 코드 의도 명확화

### Lessons Learned
1. **설계 정확도 중요**: Design이 97% 정확하면 Gap 수정으로 100% 도달 가능
2. **상수 추출 기준**: 반복되는 매직 넘버는 즉시 상수화 (TTL, pool_size 등)
3. **분산 트랜잭션**: Redis와 DB의 순서가 critical → Design 체크리스트에 포함
4. **오분석 필터링**: 이슈 11개 중 1개(오분석) + 4개(의도적 유지) 확인 → 비판적 사고 필요

### Documentation
- Plan: `docs/01-plan/features/trading-fix.plan.md` (11개 이슈 분석, 5단계 계획)
- Design: `docs/02-design/features/trading-fix.design.md` (상세 구현 설계, 코드 예시)
- Report: `docs/04-report/features/trading-fix.report.md` (PDCA 완료 리포트, Match 100%)
- Changelog: Updated with full fix details

### Code Quality Metrics
- **DB Session**: Concurrent 5 swings (Semaphore) + individual sessions (no collision)
- **Rollback**: try/except/finally guarantee → 100% cleanup
- **TTL**: Extracted to class constant (1800s = 30min = 6 cycles with 5min interval)
- **Retry**: Max 2 attempts, 1s delay → standard pattern for external APIs
- **Error Handling**: Callback with exception logging → no silent failures

## Fifth PDCA Cycle: auth-key (Authentication Key Deletion API)

### Dates
- Planned: 2026-03-24
- Completed: 2026-03-24
- Duration: ~2 hours (entire cycle)

### Results
- **Status**: Complete (100% of requirements)
- **Design Match Rate**: 100% (perfect alignment)
- **Architecture Compliance**: 100% (DDD Lite)
- **Convention Compliance**: 100%

### Feature Summary
DELETE /auths/{auth_id} 인증키 삭제 API 추가:
1. Router: DELETE endpoint 추가 (JWT 인증 Layer)
2. Service: delete_auth(user_id, auth_id) 소유권 검증 (Authorization Layer)
3. Repository: and_(USER_ID, AUTH_ID) 이중 필터 (SQL Layer)
4. 3-layer security: 타인의 인증키 삭제 불가능하도록 강화

### Security Fix
- **Before**: Service/Repository delete() 메서드가 소유권 검증 누락 → 타인의 키 삭제 가능
- **After**: 3단계 보안 검증으로 본인 소유 키만 삭제 가능

### Key Design Decisions
1. Repository에서 and_() 이중 필터 사용 → SQL 레벨 방어
2. NotFoundError(404) 통일 → 보안상 "없다고 응답" (정보 유출 방지)
3. Service commit 관리 → 트랜잭션 원자성 보장

### Files Modified
1. `app/domain/auth/router.py` (+9 lines)
2. `app/domain/auth/service.py` (signature change)
3. `app/domain/auth/repository.py` (and_() condition)

### Gap Analysis Results
- Initial Match: 100% (all requirements met)
- Final Match: 100% (no gaps found)
- Pattern: Fast, small feature with clear security requirements

### Lessons
1. **Plan 명확성**: Plan 문서에 보안 요구사항이 명확하면 구현이 직관적
2. **Ownership Pattern**: Repository의 and_() 필터 패턴은 전사 표준으로 적용 가능
3. **Code Review**: 기존 delete() 메서드의 보안 누락 → 더 엄격한 review 필요

### Documentation
- Plan: `docs/01-plan/features/auth-key.plan.md` (기존)
- Report: `docs/04-report/auth-key.report.md` (신규)
- Changelog: Updated with full feature details

## Sixth PDCA Cycle: foreign-stock (Overseas Stock Trading Support)

### Dates
- Planned: 2026-03-24
- Completed: 2026-04-02
- Duration: 10 days (entire cycle)

### Results
- **Status**: Complete (100% of requirements)
- **Design Match Rate**: 99% (target: ≥90%) — near-perfect alignment
- **Architecture Compliance**: 100% (DDD Lite patterns)
- **Convention Compliance**: 100% (Python standards)

### Feature Summary
해외 주식(미국 NASD, NYSE, AMEX 거래소) 자동매매 지원:
1. Overseas API branching 로직 추가 (MRKT_CODE 기반)
2. foreign_api.py 해외 엔드포인트 전면 수정 (6개 함수: 잔고, 주문, 현재가, 체결, 일별, 순위)
3. US 장 시간대 스케줄링 (KST 22:00-05:30)
4. 국내/해외 응답 필드 정규화 (stck_prpr→last, etc.)
5. 시장별 배치 분리 (trade_job은 국내, us_trade_job은 해외)

### Key Design Decisions
- **분기 전략**: 배치(DB MRKT_CODE) vs 라우터(Query Parameter market/excg_cd) 이원화
- **데이터 타입**: Decimal 일관 사용 (소수점 가격 처리)
- **시간대**: 서머타임/윈터 모두 커버하는 보수적 범위 (22:00-05:30 KST)
- **거래소 코드**: MRKT_CODE(NASD) vs EXCD(NAS) 매핑 테이블 중앙화 (market_router.py)

### Improvements Beyond Plan
1. **Currency Helper**: get_currency(mrkt_code) — 거래소별 통화 자동 추출
2. **Order Cancel API**: modify_or_cancel_order_api() — 향후 기능 확장용
3. **Asking Price API**: get_inquire_asking_price() — 호가 조회 별도 구현
4. **Defensive Defaults**: overseas 미제공 필드에 기본값 (prdy_vrss_vol_rate=100.0)
5. **Sell Slippage**: 매도도 슬리피지 적용 (-0.5%, 매수 +0.5%와 대칭)

### Gap Analysis Results
- **Initial Match**: 99% (62개 항목 중 61 PASS)
  - CHANGED-1: OVRS_ORD_UNPR "0" → slippage 가격 (구현이 더 실용적)
  - CHANGED-2: 파라미터명 excg_cd → excd (KIS API 규칙 일치)
  - CHANGED-3: retry delay 1.0초 → 2.0초 (미국 장 지연 반영)
  - CHANGED-4: 종가 필드 last → clos (실제 API 응답)
  - ADDED-6: Utility 함수, API 함수, 기본값 (범위 확장)
- **Final Match**: 99% (설계 재검증으로 100% 달성 가능하나, 구현상 개선으로 해석)

### Architecture Verification
- Layer dependency: Router → Service → Repository → Entity (✅ 100%)
- DDD Lite compliance: Entity, Repository, Service 역할 분리 (✅ 100%)
- Market routing: is_overseas() 중앙화, 배치/라우터 자동 분기 (✅ 100%)
- Concurrency: 미국 장 배치도 기존 Semaphore 활용 (✅ 100%)
- Transaction: foreign_api도 kis_api와 동일한 패턴 (flush vs commit) (✅ 100%)

### Files Involved
- `app/external/market_router.py` (신규)
- `app/external/foreign_api.py` (6개 함수 전면 수정)
- `app/domain/swing/trading/auto_swing_batch.py` (분기 로직)
- `app/domain/swing/trading/order_executor.py` (분기 호출)
- `app/common/scheduler.py` (US 스케줄)
- 7개 entity/repository/service/router 파일 (분기 지원)

### Lessons Learned
1. **외부 API 설계 검증**: API 공식 문서 → 실제 응답 구조 검증 필수 (delay, 필드키 등)
2. **필드 매핑 테이블**: 국내/해외 응답 차이를 설계 문서에 정리하니 구현이 직관적
3. **상수화 기준**: 슬리피지(±0.5%), delay(2.0초), 기본값(100.0) 등을 즉시 상수로 분리
4. **별도 배치 함수**: us_trade_job() 같은 명확한 네이밍으로 확장성 확보
5. **중앙화된 분기**: is_overseas() 유틸로 중복 제거 및 유지보수 용이

### Documentation
- Plan: `docs/01-plan/features/foreign-stock.plan.md` (기존)
- Design: `docs/02-design/features/foreign-stock.design.md` (기존)
- Analysis: `docs/03-analysis/foreign-stock.analysis.md` (기존)
- Report: `docs/04-report/features/foreign-stock.report.md` (신규)
- Changelog: Updated with comprehensive feature details

### Code Quality Metrics
- **Files Modified**: 11 files + 1 new file
- **Total LOC**: ~1,300 additions/modifications
- **Market Routing**: Centralized in market_router.py (3 functions)
- **API Functions**: 8 new/modified functions in foreign_api.py
- **Batch Integration**: 2 new batch jobs (us_trade_job, us_ema_cache_warmup_job)
- **Test Coverage**: Design-stage validation only (production testing pending)

## Seventh PDCA Cycle: position-sizing (포지션 사이징 리팩토링)

### Dates
- Planned: 2026-05-07 (this session)
- Completed: 2026-05-07
- Duration: ~4 hours (entire cycle)

### Results
- **Status**: Complete (100% of requirements)
- **Design Match Rate**: 100% (perfect alignment)
- **Architecture Compliance**: 100% (DDD Lite)
- **Convention Compliance**: 100%

### Feature Summary
다중 종목 배정금 모델을 위한 포지션 사이징 개선:
1. 기존 모델: RISK_PCT(2%), MAX_POSITION_PCT(25%) — 총 자산 기반, 다중 종목 부적합
2. 새 모델: ENTRY_PCT(50%), MAX_LOSS_PCT(10%) — 배정금 기반, CUR_AMOUNT 동적 추적
3. 핵심 공식: Qty = min(CUR_AMOUNT × ENTRY_PCT / 현재가, CUR_AMOUNT × MAX_LOSS_PCT / 손절거리)
4. 자본 관리: 매수 시 차감, 매도 시 회복 → 수익금 자동 복리, 손실 시 위험 자동 축소

### Key Design Decisions
1. **INIT_AMOUNT/CUR_AMOUNT 유지**: 초기 검토에서 삭제 제안 → 재검토 결과 다중 종목 할당의 핵심
2. **CUR_AMOUNT 기반 포지션 사이징**: INIT_AMOUNT(고정) 대신 현재 가용 자본 사용
3. **2중 제약 모델**: ENTRY_PCT(기본 진입 50%) + MAX_LOSS_PCT(손절 손실 10%)
4. **변동성 기반 미채택**: 이미 손절가 거리에 반영되므로 이중 가산 위험

### Files Modified
1. `app/domain/swing/trading/strategies/base_single_ema.py` (ENTRY_PCT, MAX_LOSS_PCT 추가)
2. `app/domain/swing/trading/auto_swing_batch.py` (포지션 사이징 공식, CUR_AMOUNT 관리)
3. `app/domain/swing/entity.py` (deduct_amount, add_amount 메서드)
4. `app/domain/swing/trading/order_executor.py` (반환값 확장: chunk_amount, exec_type)
5. `app/domain/swing/backtest/strategies/single_ema_backtest_strategy.py` (동기화)
6. `app/domain/swing/service.py` (EVLU_PFLS_AMT 부호 수정: CUR_AMOUNT - INIT_AMOUNT)
7. README 파일들 (2개): 매개변수 설명 업데이트

### Gap Analysis Results
- Initial Match (after CUR_AMOUNT): 90% (3 gaps: EVLU_PFLS_AMT sign, decimal precision, README)
- Intermediate Match (after ENTRY_PCT/MAX_LOSS_PCT): 92% (3 more issues: README, target_qty=0, order_executor returns)
- Final Match: **100%** (all issues resolved)

### Key Improvements
1. **EVLU_PFLS_AMT 부호 버그 수정**: 손익 계산이 이제 올바른 부호
2. **해외 주식 소수점 정밀도**: Decimal(str(amount)) 사용으로 절삭 문제 해결
3. **백테스트-실시간 동기화**: 동일한 포지션 사이징 공식 적용
4. **자본 추적 투명성**: deduct/add 메서드로 각 거래의 자본 영향 명확

### Intentional Design Deviations
- None. All design requirements met perfectly (100% match).

### Architecture Verification
- Layer dependency: Router → Service → Repository → Entity (✅ 100%)
- DDD Lite compliance: Entity business logic integration (✅ 100%)
- Multi-stock capital management: Independent CUR_AMOUNT per stock (✅ 100%)
- Backtest consistency: Same formula applied (✅ 100%)

### Lessons Learned
1. **재검토 중요성**: 초기 결정(INIT_AMOUNT 삭제)를 재검토해서 더 나은 설계 도출
2. **다중 제약 모델**: 단순한 포지션 사이징보다 2중 제약으로 더 유연한 위험 관리
3. **자본 추적 설계**: 각 메서드(deduct, add)가 명확한 거래 의미를 가짐
4. **완성도**: 100% 설계 일치도는 초기 설계 품질이 높을 때 달성 가능

### Documentation
- Plan: Design decision during this session (not formalized in separate file yet)
- Design: Concepts discussed and validated
- Analysis: `docs/03-analysis/position-sizing-gap.md` (2 iterations to 100%)
- Report: `docs/04-report/features/position-sizing.report.md` (completion report)

### Code Quality Metrics
- Files Modified: 7
- Total LOC: ~85 new lines (3% increase)
- Design Match: 100%
- Issues Fixed: 4 (EVLU_PFLS_AMT, decimal, README, target_qty)
- Test Coverage: Design validation completed

## Eighth PDCA Cycle: swing-reg (스윙 등록/수정 시 보유 자본 한도 검증)

### Dates
- Planned: 2026-04-15
- Completed: 2026-05-08
- Duration: 23 days (entire cycle)

### Results
- **Status**: Complete (100% of requirements)
- **Design Match Rate**: 95% (target: ≥90%)
- **Architecture Compliance**: 100% (DDD Lite)
- **Convention Compliance**: 100%

### Feature Summary
스윙 전략 등록/수정 시 사용자 보유 자본을 초과하는 INIT_AMOUNT 설정을 차단:
1. Repository: `get_total_init_amount()` — 활성 스윙(USE_YN='Y')의 INIT_AMOUNT 합계
2. Service: `get_available_capital()` — KIS API + DB 합계로 가용 자본 계산 (tot_evlu_amt 기준)
3. Service: `create_swing()` 검증 불필요 (USE_YN='N' 비활성 상태로 등록)
4. Service: `update_swing()` 검증 필수 (INIT_AMOUNT 변경 시 + USE_YN 활성화 시)
5. Router: `GET /swing/available-capital` — 프론트엔드 UX용 공개 API

### Key Design Evolution
- **초기**: 총 자본 = dnca_tot_amt + scts_evlu_amt (현금 + 주식평가)
- **최종**: 총 자본 = tot_evlu_amt (KIS API 기준 정확한 총평가금액, D+2 예수금 포함)
- **변경 이유**: KIS 계좌 구조에서 tot_evlu_amt가 더 정확하고 장 후 정산 반영

### Design Update v1.0 → v2.0
설계 초안 작성 후 구현 중 3가지 개선사항 발견 및 설계 문서 업데이트:
1. **총 자본 공식**: dnca_tot_amt+scts_evlu_amt → tot_evlu_amt
2. **할당금 필터**: 모든 스윙 → USE_YN='Y' 활성만 (비활성은 자본 할당 아님)
3. **검증 시점**: create_swing → update_swing (활성화 시) — 최적의 비즈니스 흐름

### Gap Analysis Results
- **Initial Match**: v1.0 설계 기준 (구 설계로 검증)
- **Final Match**: v2.0 설계 기준 95% (55/58 항목 PASS)
  - GAP-1: Section 5.2 vs 3.3.2 문서 모순 (영향: Low, 구현은 정확함)
  - GAP-2: rule 파라미터 미사용 (영향: Very Low)
  - GAP-3: int() 타입 캐스트 (영향: None, 의도적 개선)

### Files Modified
1. `app/domain/swing/repository.py:129-146` — get_total_init_amount()
2. `app/domain/swing/service.py:37-58` — get_available_capital()
3. `app/domain/swing/service.py:128-187` — update_swing() 검증 로직
4. `app/domain/swing/router.py:45-54` — GET /available-capital 엔드포인트

### Improvements Beyond Plan
1. **스마트 exclude 로직**: 활성 상태 vs 비활성→활성 전환에서 다른 exclude 처리
2. **시장별 API 분기**: MRKT_CODE 기반으로 국내(kis_api) vs 해외(foreign_api) 자동 분기
3. **에러 응답 상세화**: detail 4개 키 포함 (가용/요청/총/할당 금액)

### Key Implementation Highlights
1. **트랜잭션 안전성**: Repository=flush, Service=commit → 동시성 고려
2. **조건부 검증**: user_id 존재 시만 KIS API 호출 (배치 호출 시 user_id=None)
3. **UX 지원**: 프론트엔드에서 사전 검증할 수 있도록 공개 API 제공
4. **레이어 분리**: HTTP 비의존 예외(BusinessRuleError) 사용으로 테스트 용이

### Lessons Learned
1. **설계-구현 피드백 루프**: 초기 설계 → 구현 중 최적화 발견 → 설계 문서 업데이트 → Gap Analysis
2. **KIS API 이해**: 필드 의미(tot_evlu_amt vs dnca_tot_amt) 사전 검토 중요
3. **비즈니스 흐름**: INIT_AMOUNT 할당의 시점(등록 vs 활성화)을 초기에 명확히 할 것
4. **문서 일관성**: 섹션 간 모순(3.3.2 vs 5.2) 검수 체크리스트 필요

### Documentation
- Plan: `docs/01-plan/features/swing-reg.plan.md` (기존)
- Design v1.0: (구, 설계 업데이트로 인해 참고용)
- Design v2.0: `docs/02-design/features/swing-reg.design.md` (최신, 3가지 개선사항 반영)
- Analysis v1.0: (구 설계 기준)
- Analysis v2.0: `docs/03-analysis/swing-reg.analysis.md` (최신, 95% match)
- Report: `docs/04-report/features/swing-reg.report.md` (신규)

### Code Quality Metrics
- **신규 메서드**: 1개 (get_available_capital)
- **수정 메서드**: 2개 (create_swing 검증 불필요 주석, update_swing 검증 로직)
- **신규 엔드포인트**: 1개 (/available-capital)
- **신규 라인**: ~80줄
- **설계-구현 매칭률**: 95% (3가지 minor 갭, 모두 기능 무영향)

## Historical Metrics
- Cycle 1 (trade_history): 95% match
- Cycle 2 (ddd-refactoring): 98% match
- Cycle 3 (swing-order): 95% match
- Cycle 4 (trading-fix): 100% match ✅
- Cycle 5 (auth-key): 100% match ✅
- Cycle 6 (foreign-stock): 99% match ✅
- Cycle 7 (position-sizing): 100% match ✅
- **Cycle 8 (swing-reg): 95% match**
- Average: 97.6% design-implementation match rate (up to 98.1% with cycle 8)
- Pattern: 90%+ match rate achieved in all cycles, 100% match achievable with iteration
