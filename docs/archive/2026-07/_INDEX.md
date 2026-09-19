# Archive Index - 2026-07

| Feature | Archived | Match Rate | Documents |
|---------|----------|-----------|-----------|
| us-market-expansion | 2026-07-21 | 100% | plan, design, analysis, report |

## ⚠️ 후속 항목 (배포 전 권장)
- **us-market-expansion**: 정적 분석 100%(Gap 0). 실전/모의 계정으로 NYS/NAS/AMS 주문·잔고·시세 KIS 실호출 검증 미수행. DB `SWING_TRADE`/`STOCK_DAY_HISTORY`의 `'NASD'` 잔존은 DB 전체 초기화(2026-09-19)로 해소 — 마이그레이션 스크립트는 삭제됨(커밋 이력 없음). `AuthRepository` external→domain 위반은 별도 PDCA로 분리.
