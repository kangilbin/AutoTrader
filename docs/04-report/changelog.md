# AutoTrader Changelog

All notable changes to this project will be documented in this file.

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
