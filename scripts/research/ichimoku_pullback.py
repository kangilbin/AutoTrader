"""일목균형표 눌림목 전략 연구 (연구용 — 실전 코드와 무관)

가설: 상승 추세(구름 위·양운·기준선 상승)에서 구름 상단으로 되돌아온 눌림목을 사면,
      돌파 추격보다 진입가가 낮고 손절선(구름·기준선)이 가까워 손익 구조가 낫다.

판정 틀은 portfolio_sim.py 와 같다 (풀 공유 리스크 사이징 재생, 개발/홀드아웃 구간 분리, 시장별 따로 운용).
비교 대상은 B&H — 지수 ETF(SPY·QQQ·069500)와 같은 풀 동일가중 보유.

신호는 종가 확정 후 판정하고 다음 거래일 시가에 체결한다 (당일 봉으로 판정해 당일 체결하는 선견 금지).

변형 (2×2, 표준 기간 9/26/52 고정):
  진입  touch    구름 상단 PULLBACK_ATR 이내에 처음 들어온 날
        confirm  접근한 뒤 종가가 전환선 위로 회복한 날
  청산  cloud    종가가 구름 하단 아래로 마감
        kijun    종가가 기준선 아래로 마감

실행 (프로젝트 루트에서):
  PYTHONPATH=. uv run python scripts/research/ichimoku_pullback.py dev --tag base
  PYTHONPATH=. uv run python scripts/research/ichimoku_pullback.py dev --tag t2 --cfg '{"EXT_ATR": 2.0}'
  PYTHONPATH=. uv run python scripts/research/ichimoku_pullback.py holdout --variant V3   # 최종 후보로 단 1회

시도 기록: ichimoku_trials.log (실행마다 한 줄 — 몇 번째 시도인지가 곧 데이터 스누핑의 크기다)
"""
import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import portfolio_sim as ps  # noqa: E402  (프로젝트 루트를 sys.path 에 넣는 것도 여기서 처리된다)
from app.domain.swing.backtest.strategies.base_strategy import fill_buy, fill_sell, sell_tick  # noqa: E402

TRIAL_LOG = Path(__file__).resolve().parent / "ichimoku_trials.log"
HOLDOUT_MARKER = Path(__file__).resolve().parent / ".holdout_ichimoku_done"

TENKAN, KIJUN, SENKOU_B, SHIFT = 9, 26, 52, 26   # 표준값 — 튜닝하지 않는다
ATR_PERIOD = 14


@dataclass(frozen=True)
class Cfg:
    PULLBACK_ATR: float = 1.0      # 구름 상단까지의 거리가 이 값(ATR) 이하이면 '가까워졌다'
    EXT_ATR: float = 3.0           # 눌림 이전에 이만큼(ATR) 벌어진 적이 있어야 '올랐다가 되돌아온' 눌림
    EXT_LOOKBACK: int = 20         # 위 확장을 찾는 구간 (봉)
    CONFIRM_WINDOW: int = 10       # confirm: 접근 후 이 봉 안에 전환선을 회복해야 한다
    CONFIRM_MAX_ATR: float = 2.0   # confirm: 회복 시점에도 구름에서 이 거리(ATR) 이내여야 한다 (추격 방지)
    KIJUN_RISE_BARS: int = 5       # 기준선이 이 봉 전보다 높아야 '상승 중'


@dataclass(frozen=True)
class Variant:
    name: str
    entry: str   # touch | confirm
    exit: str    # cloud | kijun


VARIANTS = {
    "V1": Variant("V1", "touch", "cloud"),
    "V2": Variant("V2", "touch", "kijun"),
    "V3": Variant("V3", "confirm", "cloud"),
    "V4": Variant("V4", "confirm", "kijun"),
}

# 풀 구조는 실전 기본값과 같다 (집중형 10종목 · 건당 위험 0.70% · 종목당 9.5%)
POOL_KW = {"mode": "pool", "risk_pct": 0.007, "max_position_pct": 0.095, "max_positions": 10, "priority": "narrow_stop"}


# ==================== 지표 / 신호 ====================

def ichimoku_frame(df: pd.DataFrame, cfg: Cfg) -> pd.DataFrame:
    """일목 지표와 신호 열 — 모든 값은 그 봉의 종가 시점까지의 데이터만 쓴다 (선행스팬은 26봉 전에 계산된 값)"""
    o = df["STCK_OPRC"].astype(float).reset_index(drop=True)
    h = df["STCK_HGPR"].astype(float).reset_index(drop=True)
    lo = df["STCK_LWPR"].astype(float).reset_index(drop=True)
    c = df["STCK_CLPR"].astype(float).reset_index(drop=True)

    tenkan = (h.rolling(TENKAN).max() + lo.rolling(TENKAN).min()) / 2
    kijun = (h.rolling(KIJUN).max() + lo.rolling(KIJUN).min()) / 2
    sa = ((tenkan + kijun) / 2).shift(SHIFT)
    sb = ((h.rolling(SENKOU_B).max() + lo.rolling(SENKOU_B).min()) / 2).shift(SHIFT)
    top, bot = np.maximum(sa, sb), np.minimum(sa, sb)

    pc = c.shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / ATR_PERIOD, adjust=False, min_periods=ATR_PERIOD).mean()
    dist = (c - top) / atr                      # 구름 상단까지 거리 (ATR 단위, 위가 +)

    trend = (c > top) & (sa > sb) & (kijun > kijun.shift(cfg.KIJUN_RISE_BARS))
    extended = dist.shift(1).rolling(cfg.EXT_LOOKBACK).max() >= cfg.EXT_ATR

    # touch: 구름 상단 근처에 처음 들어온 날
    touch = trend & extended & (dist <= cfg.PULLBACK_ATR) & (dist.shift(1) > cfg.PULLBACK_ATR)

    # confirm: 접근 후 구름 위를 지킨 채 종가가 전환선을 위로 회복한 날
    approached = dist.shift(1).rolling(cfg.CONFIRM_WINDOW).min() <= cfg.PULLBACK_ATR
    held = (c > top).rolling(cfg.CONFIRM_WINDOW).min() == 1
    ext_before = dist.shift(1).rolling(cfg.EXT_LOOKBACK + cfg.CONFIRM_WINDOW).max() >= cfg.EXT_ATR
    reclaim = (c > tenkan) & (c.shift(1) <= tenkan.shift(1))
    confirm = trend & approached & held & ext_before & reclaim & (dist <= cfg.CONFIRM_MAX_ATR)

    out = pd.DataFrame({"o": o, "h": h, "l": lo, "c": c, "atr": atr, "tenkan": tenkan, "kijun": kijun,
                        "top": top, "bot": bot, "dist": dist,
                        "touch": touch.fillna(False), "confirm": confirm.fillna(False)})
    out["date"] = pd.to_datetime(df["STCK_BSOP_DATE"].astype(str)).reset_index(drop=True)
    return out


# ==================== 거래 기록 추출 ====================

def extract_trades(df: pd.DataFrame, mrkt_code: str, code: str, period: str, v: Variant, cfg: Cfg) -> List[ps.UnitTrade]:
    """종목 하나의 단위 거래 기록. 신호는 t 종가에 판정, 체결은 t+1 시가"""
    if not ps.is_eligible(df, period):
        return []
    start, end = ps.PERIODS[period]
    sub = df[df["STCK_BSOP_DATE"] <= end.replace("-", "")].sort_values("STCK_BSOP_DATE").reset_index(drop=True)
    f = ichimoku_frame(sub, cfg)
    adv = ps.avg_daily_amount_before(sub)
    start_ts = pd.Timestamp(start)
    line = f["bot"] if v.exit == "cloud" else f["kijun"]
    sig = f[v.entry]

    trades: List[ps.UnitTrade] = []
    cur: Optional[ps.UnitTrade] = None
    for t in range(len(f) - 1):
        nd = f["date"].iloc[t + 1]
        if cur is None:
            if nd < start_ts or not sig.iloc[t] or pd.isna(line.iloc[t]):
                continue
            entry = fill_buy(float(f["o"].iloc[t + 1]), mrkt_code)
            stop = sell_tick(float(line.iloc[t]), mrkt_code)
            if not (0 < stop < entry):
                continue
            cur = ps.UnitTrade(
                market=ps.market_of(mrkt_code), code=code, mrkt_code=mrkt_code, entry_date=nd,
                entry_price=entry, initial_stop=stop, conviction=1.0,
                adv=float(adv.get(nd, float("nan"))))
            continue
        d = f["date"].iloc[t]
        if d > cur.entry_date or d == cur.entry_date:
            prev_line = line.iloc[t - 1]
            if pd.notna(prev_line):
                cur.exit_lines[d] = float(prev_line)
        if pd.notna(line.iloc[t]) and f["c"].iloc[t] < line.iloc[t]:
            px = fill_sell(float(f["o"].iloc[t + 1]), mrkt_code)
            cur.events.append((nd, ps.EVENT_EXIT, px, 1.0))
            trades.append(cur)
            cur = None
    if cur is not None:   # 구간 마지막 날 종가로 최종 평가
        last = len(f) - 1
        cur.events.append((f["date"].iloc[last], ps.EVENT_FINAL, fill_sell(float(f["c"].iloc[last]), mrkt_code), 1.0))
        trades.append(cur)
    return trades


# ==================== 지표 / 벤치마크 ====================

def curve_metrics(equity: pd.Series, e0: float) -> Dict:
    """자산 곡선 → 총수익·CAGR·MDD·MAR·샤프 + 연도별 수익 (전략과 B&H에 같은 함수를 쓴다)"""
    if equity.empty:
        return {}
    ret = pd.concat([pd.Series([e0]), equity.reset_index(drop=True)]).pct_change().dropna()
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    cagr = (equity.iloc[-1] / e0) ** (365.25 / days) - 1
    peak = pd.concat([pd.Series([e0]), equity.reset_index(drop=True)]).cummax().iloc[1:]
    mdd = float(((peak.to_numpy() - equity.to_numpy()) / peak.to_numpy()).max())
    ye = equity.resample("YE").last()
    yearly = (pd.concat([pd.Series([e0]), ye.reset_index(drop=True)]).pct_change().dropna())
    yearly.index = ye.index.year
    return {"total": round(float(equity.iloc[-1] / e0 - 1), 4), "cagr": round(float(cagr), 4), "mdd": round(mdd, 4),
            "mar": round(float(cagr) / mdd, 2) if mdd > 0 else None,
            "sharpe": round(float(ret.mean() / ret.std() * np.sqrt(252)), 2) if ret.std() > 0 else None,
            "yearly": {int(y): round(float(r), 3) for y, r in yearly.items()}}


def bh_curve(close: pd.Series, start: str, end: str, e0: float) -> pd.Series:
    s = close[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))].dropna()
    return e0 * s / s.iloc[0]


def equal_weight_bh(closes: Dict[tuple, pd.Series], keys: List[tuple], start: str, end: str, e0: float) -> pd.Series:
    """시작일에 동일 금액을 사서 끝까지 보유 — 시작 시점에 가격이 있는 종목만"""
    cols = {}
    for k in keys:
        s = closes[k]
        s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
        if len(s) > 0 and (s.index[0] - pd.Timestamp(start)).days <= 7:
            cols[k] = s / s.iloc[0]
    panel = pd.DataFrame(cols).sort_index().ffill()
    return e0 * panel.mean(axis=1)


# ==================== 실행 ====================

def load(period: str):
    import asyncio
    universe = asyncio.run(ps.load_universe())
    data = ps.tradable(universe)
    return universe, data


def run_variant(data, period: str, v: Variant, cfg: Cfg):
    start, end = ps.PERIODS[period]
    closes = ps.closes_by_stock(data)
    out = {}
    for mk in ("KR", "US"):
        ts: List[ps.UnitTrade] = []
        for (m, c), df in data.items():
            if ps.market_of(m) == mk:
                ts.extend(extract_trades(df, m, c, period, v, cfg))
        keys = {(t.mrkt_code, t.code) for t in ts}
        p = ps.SimParams(equity0=ps.EQUITY0[mk], **POOL_KW)
        res = ps.simulate(ts, {k: s for k, s in closes.items() if k in keys}, p, start, end)
        out[mk] = {"sim": ps.sim_metrics(res), "curve": curve_metrics(res.curve["equity"], ps.EQUITY0[mk]),
                   "raw": res.curve,
                   "n_stocks": sum(1 for (m, _), df in data.items()
                                   if ps.market_of(m) == mk and ps.is_eligible(df, period))}
    return out


def benchmarks(universe, data, period: str):
    start, end = ps.PERIODS[period]
    closes = ps.closes_by_stock(universe)
    out = {"KR": {}, "US": {}}
    for mk, idx in (("KR", [("J", "069500")]), ("US", [("AMS", "SPY"), ("NAS", "QQQ")])):
        for k in idx:
            if k in closes:
                out[mk][k[1]] = curve_metrics(bh_curve(closes[k], start, end, ps.EQUITY0[mk]), ps.EQUITY0[mk])
        keys = [k for k, df in data.items() if ps.market_of(k[0]) == mk and ps.is_eligible(df, period)]
        out[mk]["동일가중"] = curve_metrics(equal_weight_bh(ps.closes_by_stock(data), keys, start, end, ps.EQUITY0[mk]),
                                         ps.EQUITY0[mk])
    return out


PARK_ETF = {"KR": ("J", "069500"), "US": ("AMS", "SPY")}
PARK_SIDE_COST = {"KR": 0.00147, "US": 0.0025 + ps.FX_COST}   # ETF 한 번 사고팔 때 편도 비용 (ETF 는 거래세 없음)


def park_overlay(curve: pd.DataFrame, etf_close: pd.Series, e0: float, side_cost: float) -> pd.Series:
    """전략 곡선의 유휴 현금(자산 − 투자금)을 지수 ETF 에 넣어 둔 합성 곡선

    일수익률 = 전략 일수익률 + 전일 현금 비중 × ETF 일수익률 − |현금 비중 변화| × 편도 비용
    근사: 현금이 늘고 줄어도 전략의 사이징(총자산 기준)과 거래는 바뀌지 않는다고 본다.
    """
    eq = curve["equity"]
    cash_w = ((curve["equity"] - curve["invested"]) / curve["equity"]).clip(lower=0)
    r_strat = pd.concat([pd.Series([e0]), eq.reset_index(drop=True)]).pct_change().dropna()
    r_strat.index = eq.index
    r_etf = etf_close.reindex(eq.index, method="ffill").pct_change().fillna(0.0)
    w_prev = cash_w.shift(1).fillna(1.0)       # 첫날은 전액 현금이었다
    cost = (cash_w - w_prev).abs() * side_cost
    r = r_strat + w_prev * r_etf - cost
    return e0 * (1 + r).cumprod()


def line(m: Dict) -> str:
    return (f"총 {m['total']:+.1%} · CAGR {m['cagr']:+.1%} · MDD {m['mdd']:.1%} · MAR {m['mar']} · 샤프 {m['sharpe']}")


def years(m: Dict) -> str:
    return " ".join(f"{y}:{r:+.0%}" for y, r in m["yearly"].items())


def trial_number() -> int:
    return sum(1 for _ in TRIAL_LOG.open()) + 1 if TRIAL_LOG.exists() else 1


def dev(cfg: Cfg, tag: str, only: Optional[List[str]]) -> None:
    universe, data = load("dev")
    bm = benchmarks(universe, data, "dev")
    start, end = ps.PERIODS["dev"]
    print(f"개발 구간 {start} ~ {end} · 시도 #{trial_number()} · cfg {asdict(cfg)}")
    print("\n=== B&H 비교 대상 ===")
    for mk in ("KR", "US"):
        for name, m in bm[mk].items():
            print(f"[{mk}] {name:<6} {line(m)}\n        {years(m)}")
    results = {}
    for name, v in VARIANTS.items():
        if only and name not in only:
            continue
        r = run_variant(data, "dev", v, cfg)
        results[name] = r
        print(f"\n=== {name} (진입 {v.entry} / 청산 {v.exit}) ===")
        for mk in ("KR", "US"):
            s, c = r[mk]["sim"], r[mk]["curve"]
            print(f"[{mk}] {line(c)}\n      거래 {s['trades']}건(신호 {s['signals']}) · 승률 {s['win_rate']} · 평균R {s['mean_r_net']}"
                  f" · 노출 {s['exposure']:.0%} · 건너뜀 {s['skipped']}\n      {years(c)}")
    with TRIAL_LOG.open("a") as fh:
        row = {"at": datetime.now().isoformat(timespec="seconds"), "tag": tag, "cfg": asdict(cfg),
               "variants": {n: {mk: {"cagr": r[mk]["curve"]["cagr"], "mdd": r[mk]["curve"]["mdd"],
                                     "mar": r[mk]["curve"]["mar"], "trades": r[mk]["sim"]["trades"]}
                                for mk in r} for n, r in results.items()}}
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\n시도 기록 추가: {TRIAL_LOG}")


def park(variant: str, cfg: Cfg, tag: str, period: str = "dev") -> Dict:
    """전략 + 유휴 현금 ETF 파킹 합성 vs ETF 단독 보유 (같은 구간)"""
    universe, data = load(period)
    start, end = ps.PERIODS[period]
    closes = ps.closes_by_stock(universe)
    r = run_variant(data, period, VARIANTS[variant], cfg)
    print(f"{period} {start} ~ {end} · {variant} · 시도 #{trial_number()} · cfg {asdict(cfg)}")
    summary = {}
    for mk in ("KR", "US"):
        e0 = ps.EQUITY0[mk]
        etf = PARK_ETF[mk]
        base = curve_metrics(bh_curve(closes[etf], start, end, e0), e0)
        alone = r[mk]["curve"]
        combo_curve = park_overlay(r[mk]["raw"], closes[etf], e0, PARK_SIDE_COST[mk])
        combo = curve_metrics(combo_curve, e0)
        s = r[mk]["sim"]
        print(f"\n[{mk}] {etf[1]} 단독 보유   {line(base)}\n      {years(base)}")
        print(f"[{mk}] 전략 단독(현금 0%) {line(alone)}\n      {years(alone)}")
        print(f"[{mk}] 전략 + 파킹      {line(combo)}\n      {years(combo)}"
              f"\n      거래 {s['trades']}건 · 승률 {s['win_rate']} · 평균R {s['mean_r_net']} · 평균 노출 {s['exposure']:.0%}")
        wins = sum(1 for y, v in combo["yearly"].items() if v > base["yearly"].get(y, -9))
        print(f"      ETF 대비: 연도 {wins}/{len(combo['yearly'])}승 · CAGR {combo['cagr'] - base['cagr']:+.1%}p"
              f" · MDD {combo['mdd'] - base['mdd']:+.1%}p · MAR {combo['mar']} vs {base['mar']}")
        summary[mk] = {"etf": base, "combo": combo, "alone": alone}
    if period == "dev":
        with TRIAL_LOG.open("a") as fh:
            fh.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "tag": tag, "mode": "park",
                                 "variant": variant, "cfg": asdict(cfg),
                                 "result": {mk: {"combo_cagr": v["combo"]["cagr"], "combo_mdd": v["combo"]["mdd"],
                                                 "combo_mar": v["combo"]["mar"], "etf_cagr": v["etf"]["cagr"],
                                                 "etf_mar": v["etf"]["mar"]} for mk, v in summary.items()}},
                                ensure_ascii=False) + "\n")
    return summary


def holdout(variant: str, cfg: Cfg, force: bool) -> None:
    if HOLDOUT_MARKER.exists() and not force:
        raise SystemExit(f"홀드아웃은 이미 한 번 실행했다 ({HOLDOUT_MARKER}). 결과를 보고 설정을 바꿔 다시 맞추지 않는다.")
    universe, data = load("holdout")
    bm = benchmarks(universe, data, "holdout")
    r = run_variant(data, "holdout", VARIANTS[variant], cfg)
    start, end = ps.PERIODS["holdout"]
    print(f"홀드아웃 {start} ~ {end} · {variant} · cfg {asdict(cfg)}")
    for mk in ("KR", "US"):
        print(f"[{mk}] 전략  {line(r[mk]['curve'])}\n      {years(r[mk]['curve'])}")
        for name, m in bm[mk].items():
            print(f"[{mk}] {name:<6} {line(m)}")
    HOLDOUT_MARKER.write_text(f"{datetime.now().isoformat()} {variant} {asdict(cfg)}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["dev", "park", "holdout"])
    ap.add_argument("--cfg", default="{}", help='Cfg 덮어쓰기 JSON, 예: \'{"EXT_ATR": 2.0}\'')
    ap.add_argument("--tag", default="", help="시도 기록에 남길 메모")
    ap.add_argument("--only", help="쉼표로 구분한 변형만 실행 (예: V3,V4)")
    ap.add_argument("--variant", help="holdout 전용 — 최종 후보 변형")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cfg = Cfg(**json.loads(a.cfg))
    if a.command == "dev":
        return dev(cfg, a.tag, a.only.split(",") if a.only else None)
    if not a.variant:
        raise SystemExit("--variant 가 필요하다")
    if a.command == "park":
        park(a.variant, cfg, a.tag)
        return
    return holdout(a.variant, cfg, a.force)


if __name__ == "__main__":
    main()
