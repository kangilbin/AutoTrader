# Gap Detector Memory - AutoTrader

## Project Architecture
- **Type**: DDD Lite + Layered Architecture (FastAPI backend)
- **Layers**: Router -> Service -> Repository -> Entity/Model
- **DI Pattern**: All routers use `get_{domain}_service` factory functions with `Depends()`
- **DB Session**: `get_db` is in `app/common/database.py` (not `dependencies.py` despite CLAUDE.md)
- **Models**: Now in domain entity.py files (post DDD-refactoring), NOT in database.py
- **Entity Pattern**: ORM model + business logic in single class (SwingTrade, User, Account, etc.)

## Conventions Confirmed
- All domain routers use Depends factory pattern (verified across 9 domains)
- Service layer should NOT contain direct SQLAlchemy `select()` queries
- Repository handles flush only; Service handles commit/rollback
- Lazy imports inside functions are acceptable for circular import prevention
- Import order in repo files: external before stdlib is a project-wide pattern (minor)
- Entity classes use Column `comment` for documentation (post-refactoring convention)

## Previous Analyses
- **trade_history v1.0** (archived 2026-03): year-based, 95% match, 2 issues (Service direct query)
- **trade_history v2.0** (2026-03-13): start_date/end_date refactor, 95% overall, 100% architecture
- **ddd-refactoring v1.0** (2026-03-14): 85% overall, 100% architecture/convention
  - Core refactoring (Steps 1-5) all 100% - ORM separation, entity business logic, orchestrator
  - Dead code cleanup INCOMPLETE: 23/25 items remaining
- **ddd-refactoring v2.0** (2026-03-14): 98% overall after Iteration 1 dead code cleanup
  - Dead code: 27/29 deleted, 2 justified deviations (order/entity.py dataclass, get_inquire_daily_ccld_obj)
  - get_inquire_daily_ccld_obj() NOT dead: used by check_order_execution() in kis_api.py L338
  - order/entity.py dataclass NOT dead: used by kis_api.py + order_executor.py
  - Design doc needs update: remove those 2 from deletion targets, add factory methods
  - Added: User.create_oauth_user(), Account.create(), Auth.create() factory methods (not in design)
- **swing-order v1.0** (2026-03-15): 95% overall, Plan 6-col refined to 2-col (TOTAL_FEE, REALIZED_PNL)
  - All 14 items 100% implemented against refined requirements
  - Minor: _calculate_sell_pnl() has direct SQLAlchemy query (cross-domain pragmatic choice)
  - Plan doc outdated: still shows 6-column original, needs update to 2-column refined version
  - Implementation adds defensive checks not in plan (entry_price null, sell_qty>0)

## Key Files
- Design docs: `docs/02-design/features/ddd-refactoring.design.md`
- Analysis: `docs/03-analysis/ddd-refactoring.analysis.md`
- Core implementation: `app/common/database.py`, `app/domain/*/entity.py`
- Orchestrator: `app/domain/swing/trading/auto_swing_batch.py`
- Strategy base: `app/domain/swing/trading/strategies/base_trading_strategy.py`
