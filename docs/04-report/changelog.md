# AutoTrader Changelog

All notable changes to this project will be documented in this file.

## [2026-04-02] - Overseas Stock Trading Support (foreign-stock)

### Added
- Overseas stock trading support for US exchanges: NASD (Nasdaq), NYSE (New York), AMEX (American)
- `external/market_router.py`: New utility module for market classification and API routing
  - `is_overseas(mrkt_code)`: Market type detection
  - `to_excd(mrkt_code)`: SWING_TRADE.MRKT_CODE → KIS EXCD parameter conversion
  - `get_currency(mrkt_code)`: Exchange → Currency mapping (USD for all US exchanges)
- `foreign_api.py`: Complete rewrite of overseas stock API functions
  - `get_stock_balance()`: Overseas account balance inquiry (TTTS3012R/VTTS3012R)
  - `place_order_api()`: Overseas order execution (JTTT1002U buy, JTTT1006U sell)
  - `get_inquire_price()`: Overseas stock quote retrieval (HHDFS00000300)
  - `check_order_execution()`: Overseas execution confirmation with retry logic (2 attempts, 2-second delay)
  - `get_inquire_daily_ccld_obj()`: Overseas unfilled order history
  - `get_inquire_asking_price()`: Overseas bid/ask inquiry
  - `get_fluctuation_rank()`, `get_volume_rank()`, `get_volume_power_rank()`: Overseas ranking APIs
- Dual-batch scheduling for US market (KST 22:00-05:30)
  - `us_ema_cache_warmup_job()`: US market EMA indicator cache warming (22:00 KST, Mon-Fri)
  - `us_trade_job()`: US market swing trading execution (23:00-05:25 KST, Mon-Sat)
- `SWING_TRADE.MRKT_CODE` validation extended with overseas codes: NASD, NYSE, AMEX
- `Order.excg_cd` field: Overseas exchange code for international order execution
- Market-filtered swing retrieval: `get_active_overseas_swings()`, `get_active_domestic_swings()`
- Stock router enhancements: `market` and `excg_cd` query parameters for ranking/quote APIs
- Response field normalization for overseas data:
  - Price: `stck_prpr` (domestic) → `last` (overseas)
  - High: `stck_hgpr` (domestic) → `high` (overseas)
  - Low: `stck_lwpr` (domestic) → `low` (overseas)
  - Volume: `acml_vol` (domestic) → `tvol` (overseas)
  - Change rate: `prdy_ctrt` (domestic) → `rate` (overseas)

### Changed
- `auto_swing_batch.py`: Added market type branching for price queries and data collection
  - `_to_excd()` conversion function for MRKT_CODE → EXCD mapping
  - Field mapping logic for domestic/overseas response normalization
  - All OrderExecutor calls now include `mrkt_code` parameter
- `order_executor.py`: Dual-API order execution with automatic routing
  - `execute_buy_with_partial(..., mrkt_code)`: Market-aware order execution
  - `execute_sell_with_partial(..., mrkt_code)`: Market-aware order execution
  - `_check_execution_with_retry_overseas()`: Extended retry logic for overseas execution confirmation
  - Order price handling: Domestic (int) vs Overseas (Decimal) with slippage (±0.5%)
- `stock/router.py`: Added market/exchange code branching for ranking and price endpoints
  - `fluctuation_rank()`: `market` parameter routes to kis_api or foreign_api
  - `volume_rank()`, `volume_power_rank()`, `get_asking_price()`: Market-aware routing
- `scheduler.py`: Extended scheduling for US market trading hours
  - Domestic trading: 10:00-15:20 KST (Mon-Fri) — unchanged
  - US trading: 22:00-05:30 KST (Mon-Sat) — added

### Fixed
- Overseas API endpoint migration: foreign_api.py was using domestic endpoints → corrected to /overseas-stock and /overseas-price paths
- Exchange code parameterization: Removed hardcoded "NASD" values → now configurable via MRKT_CODE
- TR_ID corrections: Updated overseas-specific transaction IDs for all foreign_api functions
- Price unit handling: Introduced Decimal type for accurate decimal (dollar) pricing vs integer (won) pricing

### Documentation
- [Plan Document](../01-plan/features/foreign-stock.plan.md): Feature planning, market branching strategy, scheduling
- [Design Document](../02-design/features/foreign-stock.design.md): 9-component architecture design with response field mappings
- [Analysis Document](../03-analysis/foreign-stock.analysis.md): Gap analysis with 99% design-implementation match rate
- [Completion Report](../04-report/features/foreign-stock.report.md): Final PDCA cycle results, implementation details, lessons learned

### Metrics
- **Design Match Rate**: 99% (62/62 items)
  - 4 justified changes (slippage pricing, parameter naming, retry delay, field key)
  - 6 scope expansions (currency helper, order cancel, asking price, etc.)
  - 0 missing items
- **Architecture Compliance**: 100% (DDD Lite patterns maintained)
- **Convention Compliance**: 100% (Python naming, import order, folder structure)
- **Files Modified**: 11 files
- **New Files**: 1 file (market_router.py)
- **Lines of Code**: ~1,300 additions/modifications

### Files Modified
1. `app/external/market_router.py` — NEW: Market classification utilities
2. `app/external/foreign_api.py` — Complete rewrite: 6 overseas API functions
3. `app/external/kis_api.py` — Import organization
4. `app/domain/order/entity.py` — Added excg_cd field
5. `app/domain/swing/entity.py` — Extended MRKT_CODE validation
6. `app/domain/swing/repository.py` — Added find_active_by_market_type()
7. `app/domain/swing/service.py` — Added get_active_overseas_swings()
8. `app/domain/swing/router.py` — Market filtering
9. `app/domain/swing/trading/auto_swing_batch.py` — Market branching + field mapping
10. `app/domain/swing/trading/order_executor.py` — Market-aware order execution
11. `app/common/scheduler.py` — US market scheduling

### Known Limitations (Future Scope)
- Premarket/aftermarket trading not supported (regular trading hours only)
- Other overseas exchanges (Hong Kong, Japan, China, Vietnam) not yet implemented
- FX-PnL integration not included (USD basis only)
- Daylight saving time handling is manual (not auto-detected)
- Overseas-specific trading strategies not implemented (sharing domestic strategies)

### Testing & Deployment
- Requires KIS API sandbox testing before production deployment
- Recommend staging environment validation during US market hours (mock/real account)
- Monitor logs during first week of US market trading for API response patterns
- Consider reducing schedule range after observing actual execution patterns

---

## [2026-03-24] - Auth Key Deletion API Security Fix (auth-key)

### Added
- `DELETE /auths/{auth_id}` endpoint for authentication key deletion
- Ownership validation in AuthService.delete_auth(user_id, auth_id)
- 3-layer security verification: JWT authentication → user_id validation → SQL WHERE clause

### Changed
- AuthService.delete_auth() signature: `delete_auth(auth_id)` → `delete_auth(user_id, auth_id)` with ownership check
- AuthRepository.delete() signature: `delete(auth_id)` → `delete(user_id, auth_id)` with and_() condition

### Fixed
- Security vulnerability: Authentication keys could be deleted by other users → Fixed with ownership validation

### Documentation
- [Plan Document](../01-plan/features/auth-key.plan.md): Feature planning and security requirements
- [Completion Report](../auth-key.report.md): Final PDCA cycle results and architecture details

### Metrics
- Design Match Rate: 100% (all requirements met)
- Architecture Compliance: 100% (DDD Lite patterns)
- Convention Compliance: 100% (Python standards)
- Security Verification: 3-layer validation implemented

### Files Modified
1. `app/domain/auth/router.py` - DELETE endpoint implementation
2. `app/domain/auth/service.py` - delete_auth() with ownership validation
3. `app/domain/auth/repository.py` - delete() with and_() filter condition

---

## [2026-03-22] - Swing Batch Stability Improvements (trading-fix)

### Fixed
- DB session concurrency bug: Each swing now processes with independent database session (Issue #1 CRITICAL)
- Missing rollback on exception: Added try/except/finally with rollback guarantee (Issue #2 CRITICAL)
- Trading time gap 15:00-15:30: Extended scheduler to support 15:00-15:20 exit-only trading (Issue #3 HIGH)
- Partial execution Redis-DB state mismatch: Reordered operations - DB commit first, then Redis save (Issue #4 HIGH)
- Order execution confirmation fallback: Added retry logic (2 attempts, 1-second delay) with unconfirmed flag (Issue #6 HIGH)
- EMA entry state TTL loss: Extracted TTL constant from 900s to 1800s (30 minutes) (Issue #8 MEDIUM)
- Push notification fire-and-forget exception: Added done_callback with exception logging (Issue #9 MEDIUM)
- Trading strategy factory cleanup: Removed commented-out 'A', 'B' strategies (Issue #11 LOW)

### Changed
- `process_single_swing()` signature: `(swing_row, swing_service, redis_client)` → `(swing_row, redis_client)` with per-swing session creation
- `execute_buy_with_partial()` return value: Added `partial_state` field for caller-managed Redis operations
- `execute_sell_with_partial()` return value: Added `partial_state` field for caller-managed Redis operations
- `continue_partial_execution()` return value: Added `clear_partial` flag for caller-managed Redis cleanup
- Scheduler: Added secondary job for 15:00-15:20 trading window (minute='0,5,10,15,20', hour='15')

### Documentation
- [Plan Document](../01-plan/features/trading-fix.plan.md): Analysis of 11 issues and 5-stage implementation plan
- [Design Document](../02-design/features/trading-fix.design.md): Detailed technical design with code examples
- [Completion Report](../features/trading-fix.report.md): Final PDCA cycle results and lessons learned

### Metrics
- Design Match Rate: 100% (97% initial → 100% after Gap fixes)
- Issues Fixed: 11 total (CRITICAL 2, HIGH 3, MEDIUM 3, LOW 1)
- Issues Deferred (design intent): 4 (Issue #5 misanalysis, #7 architectural limit, #10 intentional, #12 normal)
- Files Modified: 5
- Code Quality: All design items verified and implemented

### Files Modified
1. `app/domain/swing/trading/auto_swing_batch.py` - DB session separation, rollback, notification exception handling
2. `app/common/scheduler.py` - Extended trading window scheduler
3. `app/domain/swing/trading/order_executor.py` - Redis-DB ordering, retry logic
4. `app/domain/swing/trading/strategies/single_ema_strategy.py` - TTL constant extraction
5. `app/domain/swing/trading/trading_strategy_factory.py` - Code cleanup

### Verification
- Initial Design Match: 97% (28/30 items)
  - GAP-1 (Medium): Missing Redis save during partial execution → Fixed
  - GAP-2 (Low): TTL constant not extracted → Fixed
- Final Design Match: 100% (30/30 items)

---

## [2026-03-13] - Trade History API Implementation

### Added
- Trade History API endpoint: `GET /trade-history/{swing_id}?year={year}`
- PriceHistoryItem schema for OHLCV data
- Ema20HistoryItem schema for EMA20 technical indicator
- TradeHistoryWithChartResponse schema for unified response
- TradeHistoryRepository.find_by_swing_id_and_year() method for year-based filtering
- TradeHistoryService.get_trade_history_with_chart() business logic
- JWT authentication and ownership validation for trade history access
- Year-based pagination support (query parameter)
- Empty data guard logic for robust error handling
- Complete PDCA cycle documentation (Plan, Design, Analysis, Report)

### Changed
- Ownership verification optimized from 2-step to 1-step database query
- Added Optional type hints for improved code clarity

### Fixed
- Added guard clause for empty price history data

### Documentation
- [Plan Document](../01-plan/features/trade_history.plan.md): Requirements and API design
- [Design Document](../02-design/features/trade_history.design.md): Technical architecture and implementation guide
- [Analysis Report](../03-analysis/trade_history.analysis.md): Gap analysis with 95% match rate
- [Completion Report](../features/trade_history.report.md): PDCA cycle results and lessons learned

### Metrics
- Design Match Rate: 95% (exceeded 90% target)
- Architecture Compliance: 95%
- Convention Compliance: 98%
- All 4 Functional Requirements (FR-1 ~ FR-4) completed

### Files Modified
1. `app/domain/trade_history/schemas.py` - Added response DTOs
2. `app/domain/trade_history/repository.py` - Added find_by_swing_id_and_year()
3. `app/domain/trade_history/service.py` - Added get_trade_history_with_chart()
4. `app/domain/trade_history/router.py` - New GET endpoint
5. `app/domain/routers/__init__.py` - Router registration
6. `app/main.py` - include_router() call

---

## Future Versions

### Planned Enhancements (v1.1.0)

- [ ] Trade history result caching (Redis, year-based)
- [ ] Extended pagination (monthly/daily filtering)
- [ ] Trading statistics (win rate, return rate, max drawdown)
- [ ] Unit and integration test coverage
- [ ] API documentation (OpenAPI/Swagger)

### Known Limitations

- No pagination beyond year-based filtering (roadmap item)
- Statistics not included in current response (future enhancement)
- Cache invalidation strategy to be defined (future)
