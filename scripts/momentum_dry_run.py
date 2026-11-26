"""상대 모멘텀 드라이런 — 주문 없이 실전과 같은 데이터 경로(DB 일봉 → 보정 → 순위)로 현재 상위 종목을 출력한다 (읽기 전용)

또 연구 패널(scripts/research/relative_momentum.py)의 같은 월말 순위와 비교해 실전 경로가 연구와 일치하는지 확인한다.

실행: PYTHONPATH=. uv run python scripts/momentum_dry_run.py [--compare-months 4]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.common.database import Database  # noqa: E402
from app.domain.momentum import ranking as rk  # noqa: E402
from app.domain.momentum.repository import MomentumRepository  # noqa: E402
from app.domain.momentum.universe import universe_names, universe_pairs  # noqa: E402
from app.domain.pool.entity import PARK_ETF  # noqa: E402

STALE_DAYS = 10    # 시장의 마지막 봉보다 이 일수 넘게 오래된 종목은 거래 불가(폐지·정지)로 보고 뺀다


async def live_universe(market: str, since: str, upto: str = None):
    """실전과 같은 경로의 순위 입력 — (종목 → 종가열, 기준일, 제외 수). upto(YYYYMMDD) 이전 봉만 쓴다 (과거 시점 재현용)"""
    db = await Database.get_session()
    try:
        await db.execute(text("SET SESSION TRANSACTION READ ONLY"))
        bars = await MomentumRepository(db).load_universe_bars(universe_pairs(market), since)
    finally:
        await db.close()
    park = PARK_ETF[market]["code"]
    closes, last_dates = {}, {}
    for (m, code), df in bars.items():
        if code == park:
            continue
        df = df[df["STCK_BSOP_DATE"] <= upto] if upto else df
        if df.empty:
            continue
        closes[code] = list(rk.clean_bars(df)["STCK_CLPR"])
        last_dates[code] = df["STCK_BSOP_DATE"].iloc[-1]
    latest = max(last_dates.values())
    fresh = {c: v for c, v in closes.items() if (pd.Timestamp(latest) - pd.Timestamp(last_dates[c])).days <= STALE_DAYS}
    return fresh, latest, len(closes) - len(fresh)


async def compare_with_research(months: int) -> None:
    import relative_momentum as rm
    import portfolio_sim as ps
    universe = await ps.load_universe()
    data = ps.tradable(universe)
    print(f"\n=== 연구 패널과 월말 순위 비교 (최근 {months}개월 말) ===")
    for mk in ("KR", "US"):
        keys = [k for k in data if ps.market_of(k[0]) == mk]
        panel = rm.make_panel(data, keys, "2030-12-31")
        idx = panel["close"].index
        for r in panel["reb"][-months - 1:-1]:
            date = idx[r].strftime("%Y%m%d")
            research = [c.split("|")[1] for c in panel["score"]["M2"].iloc[r].dropna().nlargest(rm.TOP_N).index]
            live, _, _ = await live_universe(mk, "20190101", upto=date)
            mine = [x.code for x in rk.rank_universe(live)[:rk.TOP_N]]
            same = len(set(research) & set(mine))
            tag = "순서까지 일치" if research == mine else ("집합 일치" if set(research) == set(mine) else "차이")
            print(f"[{mk}] {date}: 연구 상위 10 ∩ 실전 상위 10 = {same}/10  ({tag})")
            if same < 10:
                print(f"      연구 전용 {sorted(set(research) - set(mine))} · 실전 전용 {sorted(set(mine) - set(research))}")


async def main_async(compare_months: int) -> None:
    for mk in ("KR", "US"):
        closes, latest, stale = await live_universe(mk, "20250101")
        names = universe_names(mk)
        ranked = rk.rank_universe(closes)
        print(f"\n=== {mk} · 기준 봉 {latest} · 유니버스 {len(universe_pairs(mk))}종목 중 일봉 있음 {len(closes) + stale} "
              f"(오래된 {stale}개 제외) · 점수 보유 {len(ranked)}종목 ===")
        if not ranked:
            print("순위를 믿을 수 없음 (점수 보유 종목 부족) → 이번 달 변경 없음")
            continue
        for r in ranked[:rk.TOP_N]:
            print(f"  {r.rank:2d}위 {r.code:8s} {names.get(r.code, '')[:22]:22s} 6-1 모멘텀 {r.score:+.1%}")
        print(f"  차순위(대체 후보): {[f'{r.rank}위 {r.code}' for r in ranked[rk.TOP_N:rk.TOP_N + 5]]} ...")
    if compare_months:
        await compare_with_research(compare_months)
    await Database.close() if hasattr(Database, "close") else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare-months", type=int, default=0, help="연구 패널과 비교할 최근 월말 수 (0이면 생략)")
    a = ap.parse_args()
    asyncio.run(main_async(a.compare_months))


if __name__ == "__main__":
    main()
