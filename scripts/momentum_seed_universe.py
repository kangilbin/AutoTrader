"""상대 모멘텀 유니버스 시드 — 목록(app/domain/momentum/universe.json)의 종목이 매일 수집되도록 STOCK_INFO.DATA_YN 을 맞춘다

순위가 최신 일봉으로 계산되려면 목록의 모든 종목이 매일 수집돼야 하고, 일봉 수집 잡(day_collect_job)은 DATA_YN='Y' 인 종목만 수집한다.
기본은 **점검만** 한다 (DB 를 바꾸지 않는다). 이 DB 에서 가능한 것과 필요한 것을 구분해서 보여 준다:
  - 일봉이 충분(126+21봉 이상)한 종목: --apply 로 DATA_YN='Y' 를 켠다 (이력은 이미 있다)
  - 일봉이 모자라거나 STOCK_INFO 에 없는 종목: 이력을 먼저 적재해야 한다 (--load-missing, BATCH_USER_ID 의 KIS 시세 인증키로 3년치 적재)

실행:
  PYTHONPATH=. uv run python scripts/momentum_seed_universe.py                 # 점검만
  PYTHONPATH=. uv run python scripts/momentum_seed_universe.py --apply         # DATA_YN='Y' 켜기 (이력이 충분한 종목)
  PYTHONPATH=. uv run python scripts/momentum_seed_universe.py --apply --load-missing   # 이력 없는 종목은 KIS 에서 적재 (시세 호출 발생)
"""
import argparse
import asyncio

from sqlalchemy import text, bindparam

from app.common.database import Database
from app.domain.momentum import ranking as rk
from app.domain.momentum.universe import universe_pairs

NEED_BARS = rk.LOOKBACK_BARS + 1 + 20     # 순위에 필요한 봉 수 + 여유


async def main_async(apply: bool, load_missing: bool) -> None:
    db = await Database.get_session()
    try:
        for market in ("KR", "US"):
            pairs = universe_pairs(market)
            codes = sorted({c for _, c in pairs})
            info = {(m, c): d for m, c, d in (await db.execute(
                text("SELECT MRKT_CODE, ST_CODE, DATA_YN FROM STOCK_INFO WHERE ST_CODE IN :codes").bindparams(
                    bindparam("codes", expanding=True)), {"codes": codes})).all()}
            counts = {(m, c): n for m, c, n in (await db.execute(
                text("SELECT MRKT_CODE, ST_CODE, COUNT(*) FROM STOCK_DAY_HISTORY WHERE ST_CODE IN :codes "
                     "GROUP BY MRKT_CODE, ST_CODE").bindparams(bindparam("codes", expanding=True)), {"codes": codes})).all()}
            missing_info = [p for p in pairs if p not in info]
            enough = [p for p in pairs if counts.get(p, 0) >= NEED_BARS]
            short = [p for p in pairs if p in info and counts.get(p, 0) < NEED_BARS]
            already = [p for p in enough if info.get(p) == 'Y']
            to_flip = [p for p in enough if p in info and info[p] != 'Y']
            print(f"\n=== {market}: 목록 {len(pairs)}종목 ===")
            print(f"  STOCK_INFO 에 없음 {len(missing_info)} · 일봉 {NEED_BARS}봉 미만 {len(short)} · 일봉 충분 {len(enough)} "
                  f"(이미 수집 대상 {len(already)} · 수집 대상으로 켤 종목 {len(to_flip)})")
            if missing_info:
                print(f"  STOCK_INFO 없음: {[c for _, c in missing_info][:10]}{' ...' if len(missing_info) > 10 else ''}")
            if short:
                print(f"  일봉 부족(이력 적재 필요): {[c for _, c in short][:10]}{' ...' if len(short) > 10 else ''}")
            if apply and to_flip:
                for m, c in to_flip:
                    await db.execute(text("UPDATE STOCK_INFO SET DATA_YN='Y', MOD_DT=NOW() WHERE MRKT_CODE=:m AND ST_CODE=:c"),
                                     {"m": m, "c": c})
                await db.commit()
                print(f"  → DATA_YN='Y' 로 변경 {len(to_flip)}종목 (이후 일봉 수집 잡이 매일 수집)")
            if apply and load_missing and short:
                from app.core.config import get_settings
                from app.domain.stock.stock_data_batch import fetch_and_store_3_years_data
                user_id = get_settings().BATCH_USER_ID
                if not user_id:
                    print("  BATCH_USER_ID 미설정 → 이력 적재 불가")
                else:
                    for m, c in short:
                        print(f"  이력 적재: {m}/{c}")
                        await fetch_and_store_3_years_data(user_id, m, c, {"MRKT_CODE": m, "ST_CODE": c})
            elif short and not load_missing:
                print("  (이력 부족 종목은 --apply --load-missing 으로 적재하세요. KIS 시세 호출이 발생합니다)")
    finally:
        await db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="DATA_YN 을 실제로 바꾼다 (기본은 점검만)")
    ap.add_argument("--load-missing", action="store_true", help="--apply 와 함께: 이력이 모자란 종목을 KIS 에서 적재")
    a = ap.parse_args()
    asyncio.run(main_async(a.apply, a.load_missing))


if __name__ == "__main__":
    main()
