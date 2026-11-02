"""포트폴리오 백테스트 — 풀 공유 + 리스크 사이징 + 한도 검증 (연구용)

설계: docs/02-design/features/portfolio-risk-sizing.design.md
체크리스트: docs/01-plan/features/portfolio-risk-sizing.checklist.md

핵심 아이디어: 단일 이평선 전략의 신호·청산선·쿨다운은 자본과 무관하다.
  1) 추출  종목마다 전략을 한 번 돌려 "단위 수량 기준 거래 기록(UnitTrade)"을 뽑는다
  2) 재생  전 종목 거래 기록을 날짜순으로 재생하며 사이징·한도·우선순위를 적용한다 (재생 엔진은 이어서 구현)

구간 (기존 연구 규칙 유지, 결과 보기 전 고정):
  개발     2020-05-01 ~ 2026-10-23
  홀드아웃 2015-01-01 ~ 2019-12-31  (최종 후보로 단 1회)

실행 예 (추출 요약 — 재생 엔진 구현 전 점검용):
  PYTHONPATH=. uv run python scripts/research/portfolio_sim.py extract dev
"""
import argparse
import asyncio
import math
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.common.database import Database  # noqa: E402
from app.core.market_code import is_overseas  # noqa: E402
from app.domain.stock.service import StockService  # noqa: E402
from app.domain.swing import risk_sizing  # noqa: E402
from app.domain.swing.backtest.strategies.base_strategy import trade_costs  # noqa: E402
from app.domain.swing.backtest.strategies.single_ema_backtest_strategy import SingleEMABacktestStrategy  # noqa: E402
from app.domain.swing.trading.strategies.base_single_ema import BaseSingleEMAStrategy  # noqa: E402

PERIODS = {"dev": ("2020-05-01", "2026-10-23"), "holdout": ("2015-01-01", "2019-12-31")}
MIN_BARS = 300             # DB 적재 봉 수가 이보다 적은 종목은 제외 (워밍업 + 평가에 부족)
MIN_WARMUP_BARS = 130      # 구간 시작 전 이만큼 일봉이 없으면 그 구간에서 제외 (EMA120 워밍업 121봉 + 여유)
UNIT_QTY = 10_000          # 추출 시 진입 수량 — 정수로 반으로 나뉘는 값이어야 부분익절이 정확하다
EXTRACT_CAPITAL = 10 ** 15 # 추출용 자본 — unit_qty 매수를 항상 감당할 만큼 크게
ADV_WINDOW = 20            # 평균 거래대금 창 (실전 지표 캐시 avg_daily_amount 와 같은 정의)
FX_COST = 0.0025           # 미국 환전 비용 (매수·매도 각각, 원화 투자 기준 — 기존 시뮬레이터와 같은 값)

# 시장 코드 → 시장 구분. 국내(J)는 원화, 나머지(NYS/NAS/AMS)는 달러 기준으로 따로 운용한다
INDEX_CODES = {("J", "069500"), ("AMS", "SPY"), ("NAS", "QQQ")}   # 비교용 지수 ETF — 매매 대상에서 제외


def market_of(mrkt_code: str) -> str:
    return "US" if is_overseas(mrkt_code) else "KR"


# ==================== 데이터 적재 ====================

async def load_universe(since: str = "2013-01-01") -> Dict[tuple, pd.DataFrame]:
    """DB의 일봉 전체 (MRKT_CODE, ST_CODE) → DataFrame. 지수 ETF는 제외하지 않는다 (비교용으로도 쓴다)"""
    db = await Database.get_session()
    try:
        codes = (await db.execute(text(
            "SELECT MRKT_CODE, ST_CODE, COUNT(*) FROM STOCK_DAY_HISTORY GROUP BY MRKT_CODE, ST_CODE"))).all()
        data = {}
        for m, c, n in codes:
            if n < MIN_BARS:
                continue
            rows = await StockService(db).get_stock_history(m, c, datetime.fromisoformat(since))
            data[(m, c)] = adjust_splits(
                repair_bars(pd.DataFrame(rows).sort_values("STCK_BSOP_DATE").reset_index(drop=True)))
        return data
    finally:
        await db.close()


def repair_bars(df: pd.DataFrame) -> pd.DataFrame:
    """명백한 데이터 오류 봉 보정 — 고·저가가 시가·종가에서 터무니없이 벗어나면 시가·종가 범위로 자른다

    예: VZ 2026-01-08 종가 40.57, 저가 10.60 (KIS 원본 오류). 그대로 두면 가짜 손절이 난다.
    연구용 로드에만 적용하고 DB·서비스 데이터는 건드리지 않는다.
    """
    df = df.copy()
    for col in ("STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"):
        df[col] = df[col].astype(float)
    body_low = df[["STCK_OPRC", "STCK_CLPR"]].min(axis=1)
    body_high = df[["STCK_OPRC", "STCK_CLPR"]].max(axis=1)
    bad_low = df["STCK_LWPR"] < body_low * 0.5
    bad_high = df["STCK_HGPR"] > body_high * 2.0
    df.loc[bad_low, "STCK_LWPR"] = body_low[bad_low]
    df.loc[bad_high, "STCK_HGPR"] = body_high[bad_high]
    return df


SPLIT_MAX = 50             # 분할·병합 비율 상한 (k:1 / 1:k, 정수)
SPLIT_TOLERANCE = 0.05     # 시가 갭 비율이 가장 가까운 정수 비율에서 이 비율 이내일 때만 분할로 본다


def split_ratio(prev_close: float, open_price: float) -> Optional[float]:
    """분할일 시가 ÷ 전일 종가가 분할(1/k)·병합(k) 비율이면 그 비율, 아니면 None

    분할은 시가 갭으로 나타난다 — 종가 전일비는 분할 당일의 실제 등락(예: TSLA 5:1 당일 +12%)이 섞여 비율에서 벗어난다.
    정수 비율에 맞을 때만 분할로 본다: 실제 폭락(예: -36% = 0.64)을 분할로 오인하면 손실 거래가 사라져
    결과가 낙관적으로 왜곡된다.
    """
    if prev_close <= 0 or open_price <= 0:
        return None
    r = open_price / prev_close
    k = round(1 / r) if r < 1 else round(r)
    if k < 2 or k > SPLIT_MAX:
        return None
    target = 1 / k if r < 1 else float(k)
    return target if abs(r - target) / target <= SPLIT_TOLERANCE else None


def adjust_splits(df: pd.DataFrame) -> pd.DataFrame:
    """미보정 액면분할·병합을 소급 보정한다 — 분할일 이전의 가격에 비율을 곱하고 거래량을 나눈다

    DB 일봉에 미국 분할이 보정되지 않은 채 남아 있어(AMZN 20:1, NFLX 10:1, GE 1:8 병합 등) 가짜 손절·가짜 수익
    (−23R, +119R)을 만든다. 연구용 적재에만 적용하고 DB·서비스 데이터는 건드리지 않는다.
    거래량이 없는 봉(분사·상장 직후)은 분할인지 판단할 수 없어 건드리지 않는다.
    """
    df = df.copy()
    close = df["STCK_CLPR"].astype(float).to_numpy()
    open_ = df["STCK_OPRC"].astype(float).to_numpy()
    vol = df["ACML_VOL"].astype(float).to_numpy()
    n = len(df)
    mult = np.ones(n)
    cum = 1.0
    for i in range(n - 1, 0, -1):            # 최근 → 과거: 분할일 이전 봉에 그 비율을 누적해서 곱한다
        if not (vol[i] > 0 and vol[i - 1] > 0):
            mult[i - 1] = cum
            continue
        r = split_ratio(close[i - 1], open_[i])
        if r is not None:
            cum *= r
        mult[i - 1] = cum
    mult[n - 1] = 1.0
    for col in ("STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR"):
        df[col] = df[col].astype(float) * mult
    df["ACML_VOL"] = df["ACML_VOL"].astype(float) / mult
    return df


def tradable(data: Dict[tuple, pd.DataFrame]) -> Dict[tuple, pd.DataFrame]:
    return {k: v for k, v in data.items() if k not in INDEX_CODES}


# ==================== 거래 기록 추출 ====================

# 이벤트 종류 — 청산·부분익절은 전략이 정한 가격, 최종 청산은 구간 마지막 날 종가 평가
EVENT_PARTIAL = "PARTIAL"
EVENT_EXIT = "EXIT"
EVENT_FINAL = "FINAL"


@dataclass
class UnitTrade:
    """종목 하나의 사이클 하나 — 수량에 독립적인 거래 기록 (엔진이 수량을 곱해 재생한다)"""
    market: str                      # "KR" | "US"
    code: str
    mrkt_code: str                   # J / NYS / NAS / AMS (호가·비용 판정용)
    entry_date: pd.Timestamp
    entry_price: float               # 슬리피지 반영 체결가
    initial_stop: float              # 진입 시 청산선 — 1R 기준 (R = entry_price − initial_stop)
    conviction: float                # 우선순위 기준 (신호 강도)
    adv: float                       # 진입 시점 20일 평균 거래대금 (유동성 필터용, 전일까지)
    # (날짜, 종류, 체결가, 진입 수량 대비 비율) — 비율 합은 1
    events: List[Tuple[pd.Timestamp, str, float, float]] = field(default_factory=list)
    exit_lines: Dict[pd.Timestamp, float] = field(default_factory=dict)   # 보유 중 일별 청산선

    @property
    def exit_date(self) -> pd.Timestamp:
        return self.events[-1][0]


def avg_daily_amount_before(df: pd.DataFrame) -> pd.Series:
    """날짜 → 그 날 이전 ADV_WINDOW 일 평균 거래대금 (당일 제외 — 실전은 전일 완성봉 기준 캐시)"""
    amount = df["STCK_CLPR"].astype(float) * df["ACML_VOL"].astype(float)
    adv = amount.rolling(ADV_WINDOW).mean().shift(1)
    return pd.Series(adv.to_numpy(), index=pd.to_datetime(df["STCK_BSOP_DATE"].astype(str)))


def is_eligible(df: pd.DataFrame, period: str) -> bool:
    """구간 시작 전 워밍업 봉이 충분한 종목인가 — 그 구간 풀에 들어갈 수 있는 종목"""
    start, end = PERIODS[period]
    sub = df[df["STCK_BSOP_DATE"] <= end.replace("-", "")]
    return int((sub["STCK_BSOP_DATE"] < start.replace("-", "")).sum()) >= MIN_WARMUP_BARS


def extract_unit_trades(df: pd.DataFrame, mrkt_code: str, code: str, period: str) -> List[UnitTrade]:
    """종목 하나의 단위 거래 기록 — 구간 시작 전 워밍업이 부족하면 빈 목록"""
    if not is_eligible(df, period):
        return []
    start, end = PERIODS[period]
    sub = df[df["STCK_BSOP_DATE"] <= end.replace("-", "")]

    result = SingleEMABacktestStrategy().compute(sub.copy(), {
        "st_code": code, "mrkt_code": mrkt_code, "swing_type": "S",
        "init_amount": EXTRACT_CAPITAL, "eval_start": start,
        "unit_qty": UNIT_QTY, "research_log": True,
    })
    adv = avg_daily_amount_before(sub)
    lines = {pd.Timestamp(x["date"]): x["exit_line"] for x in result.get("exit_lines", [])}

    cycles: List[UnitTrade] = []
    cur: Optional[UnitTrade] = None
    remaining = 0
    for t in result["trades"]:
        date = pd.Timestamp(t["date"])
        if t["action"] == "BUY":
            cur = UnitTrade(
                market=market_of(mrkt_code), code=code, mrkt_code=mrkt_code,
                entry_date=date, entry_price=float(t["price"]),
                initial_stop=float(t["initial_stop"]), conviction=float(t["conviction"]),
                adv=float(adv.get(date, float("nan"))),
            )
            remaining = int(t["quantity"])
            entry_qty = remaining
            continue
        if cur is None:
            continue
        qty = int(t["quantity"])
        # 부분인지 전량인지는 사유 문자열이 아니라 남은 수량으로 판정한다
        if qty < remaining:
            kind = EVENT_PARTIAL
        elif "최종 청산" in " ".join(t.get("reasons", [])):
            kind = EVENT_FINAL
        else:
            kind = EVENT_EXIT
        cur.events.append((date, kind, float(t["price"]), qty / entry_qty))
        remaining -= qty
        if remaining <= 0:
            cur.exit_lines = {d: v for d, v in lines.items() if cur.entry_date < d <= cur.exit_date}
            cycles.append(cur)
            cur = None
    return cycles


def extract_all(data: Dict[tuple, pd.DataFrame], period: str) -> List[UnitTrade]:
    out: List[UnitTrade] = []
    for (m, c), df in data.items():
        out.extend(extract_unit_trades(df, m, c, period))
    return out


def closes_by_stock(data: Dict[tuple, pd.DataFrame]) -> Dict[tuple, pd.Series]:
    """(시장코드, 종목) → 날짜별 종가 — 엔진이 보유 평가금을 매일 계산할 때 쓴다"""
    return {k: pd.Series(df["STCK_CLPR"].astype(float).to_numpy(),
                         index=pd.to_datetime(df["STCK_BSOP_DATE"].astype(str)))
            for k, df in data.items()}


# ==================== 재생 엔진 ====================

POOL = "POOL"                      # pool 모드의 단일 현금 장부 키
SKIP_ZERO_QTY = "zero_qty"         # 수량 0 (손절폭 없음·총자산 부족) — 한도 사유(risk_sizing.REJECT_*)와 같은 집계 키


@dataclass
class SimParams:
    mode: str = "pool"                          # "pool" 풀 공유·리스크 사이징 | "fixed_alloc" 현재 구조(종목별 배정금) 재현
    equity0: float = 1_000_000_000              # 시장별 초기 총자산
    n_stocks: int = 0                           # fixed_alloc: 종목당 배정금 = equity0 / n_stocks
    risk_pct: float = 0.01
    max_position_pct: Optional[float] = None    # 종목당 최대 금액 / 총자산
    max_positions: Optional[int] = None
    max_open_risk_pct: Optional[float] = None
    priority: str = "conviction"                # "conviction" | "narrow_stop" | "random"
    seed: int = 0                               # priority="random" 일 때
    exits_first: bool = True                    # 같은 날 청산을 진입보다 먼저 처리 (파킹 ETF 매도 후 즉시 재매수 가정)
    adv_ratio: float = BaseSingleEMAStrategy.MAX_ADV_RATIO
    park_apy: float = 0.0                       # 현금에 일할 가산하는 파킹 수익률 (연)


@dataclass
class Position:
    idx: int
    trade: UnitTrade
    entry_qty: int
    qty: int
    buy_cost: float                 # 매수 대금 + 수수료 + 환전 비용
    risk_amount: float              # 진입 시 (진입가 − 초기 청산선) × 수량
    binding: str
    last_line: float                # 오늘까지 알려진 청산선
    proceeds: float = 0.0           # 누적 순매도대금


@dataclass
class SimResult:
    curve: pd.DataFrame             # 일별 equity · invested · n_pos · open_risk
    cycles: List[Dict]
    skipped: Counter
    signals: int                    # 진입 후보 총 수 (체결 + 건너뜀)
    params: SimParams


def _order(candidates: List[int], trades: List[UnitTrade], p: SimParams, rng: random.Random) -> List[int]:
    """같은 날 진입 후보의 처리 순서 — 한도가 찼을 때 어느 신호가 살아남는지를 정한다"""
    ordered = sorted(candidates, key=lambda i: (trades[i].mrkt_code, trades[i].code))   # 입력 순서에 기대지 않는다
    if p.priority == "conviction":
        return sorted(ordered, key=lambda i: -trades[i].conviction)
    if p.priority == "narrow_stop":
        return sorted(ordered, key=lambda i: (trades[i].entry_price - trades[i].initial_stop) / trades[i].entry_price)
    if p.priority == "random":
        rng.shuffle(ordered)
        return ordered
    raise ValueError(f"알 수 없는 priority: {p.priority}")


def simulate(trades: List[UnitTrade], closes: Dict[tuple, pd.Series], p: SimParams,
             start: str, end: str) -> SimResult:
    """한 시장의 거래 기록을 날짜순으로 재생한다 (KR·US는 통화·자금이 달라 따로 호출)

    closes: 이 시장 종목들의 (시장코드, 종목) → 날짜별 종가. 거래일 달력과 보유 평가에 쓴다.
    """
    if p.mode not in ("pool", "fixed_alloc"):
        raise ValueError(f"알 수 없는 mode: {p.mode}")
    if p.mode == "fixed_alloc" and p.n_stocks <= 0:
        raise ValueError("fixed_alloc 은 n_stocks 가 필요하다")

    market = trades[0].market if trades else ("KR" if not closes else market_of(next(iter(closes))[0]))
    comm, tax = trade_costs("NAS" if market == "US" else "J")
    fx = FX_COST if market == "US" else 0.0

    dates = sorted({d for s in closes.values() for d in s.index if pd.Timestamp(start) <= d <= pd.Timestamp(end)})
    di_of = {d: i for i, d in enumerate(dates)}
    aligned = {k: s.reindex(dates, method="ffill").to_numpy() for k, s in closes.items()}

    entries: Dict[pd.Timestamp, List[int]] = defaultdict(list)
    events: Dict[pd.Timestamp, List[tuple]] = defaultdict(list)
    for i, t in enumerate(trades):
        entries[t.entry_date].append(i)
        for date, kind, price, ratio in t.events:
            events[date].append((i, kind, price, ratio))

    pool = p.mode == "pool"
    ledger: Dict[object, float] = {POOL: p.equity0} if pool else {
        (t.mrkt_code, t.code): p.equity0 / p.n_stocks for t in trades}
    if not pool:   # 신호가 한 번도 없던 종목의 배정금도 현금으로 계산에 들어가야 한다
        spare = p.n_stocks - len(ledger)
        ledger["IDLE"] = max(0, spare) * p.equity0 / p.n_stocks

    positions: Dict[int, Position] = {}
    cycles: List[Dict] = []
    skipped: Counter = Counter()
    rng = random.Random(p.seed)
    rows = []
    equity_prev = p.equity0
    prev_date = None

    def key_of(t: UnitTrade):
        return POOL if pool else (t.mrkt_code, t.code)

    def open_risk() -> float:
        return risk_sizing.total_open_risk(
            [(pos.trade.entry_price, pos.last_line, pos.qty) for pos in positions.values()])

    def process_events(d, entered_today_only=False):
        for i, kind, price, ratio in events.get(d, []):
            pos = positions.get(i)
            if pos is None:        # 한도로 건너뛴 진입의 청산 이벤트
                continue
            if entered_today_only and pos.trade.entry_date != d:
                continue
            sell_qty = pos.qty if kind in (EVENT_EXIT, EVENT_FINAL) else min(pos.qty, int(pos.entry_qty * ratio))
            if sell_qty <= 0:
                continue
            amount = sell_qty * price
            net = amount * (1 - comm - tax - fx)
            ledger[key_of(pos.trade)] += net
            pos.proceeds += net
            pos.qty -= sell_qty
            if pos.qty <= 0:
                pnl = pos.proceeds - pos.buy_cost
                cycles.append({
                    "market": market, "code": pos.trade.code, "entry_date": pos.trade.entry_date, "exit_date": d,
                    "qty": pos.entry_qty, "amount": pos.entry_qty * pos.trade.entry_price, "pnl": pnl,
                    "r_net": pnl / pos.risk_amount if pos.risk_amount > 0 else 0.0, "binding": pos.binding,
                })
                del positions[i]

    def process_entries(d):
        candidates = entries.get(d, [])
        for i in _order(candidates, trades, p, rng):
            t = trades[i]
            key = key_of(t)
            if pool:
                sized = risk_sizing.calc_qty(equity_prev, p.risk_pct, t.entry_price, t.initial_stop, p.max_position_pct)
                qty, risk_amount, binding = sized.qty, sized.risk_amount, sized.binding
            else:   # 현재 구조: 종목 배정금(CUR_AMOUNT) × MAX_ENTRY_PCT × conviction
                qty = int(ledger[key] * BaseSingleEMAStrategy.MAX_ENTRY_PCT * t.conviction / t.entry_price)
                risk_amount, binding = qty * max(0.0, t.entry_price - t.initial_stop), risk_sizing.BINDING_RISK
            if qty <= 0:
                skipped[SKIP_ZERO_QTY] += 1
                continue
            amount = qty * t.entry_price
            if not risk_sizing.within_liquidity(amount, t.adv, p.adv_ratio):
                skipped[risk_sizing.REJECT_LIQUIDITY] += 1
                continue
            cost = amount * (1 + comm + fx)
            if pool:
                reason = risk_sizing.check_limits(
                    order_amount=cost, new_risk=risk_amount, cash=ledger[POOL], equity=equity_prev,
                    position_count=len(positions), open_risk=open_risk(),
                    max_positions=p.max_positions, max_open_risk_pct=p.max_open_risk_pct)
            else:
                reason = risk_sizing.REJECT_CASH if cost > ledger[key] else None
            if reason:
                skipped[reason] += 1
                continue
            ledger[key] -= cost
            positions[i] = Position(i, t, qty, qty, cost, risk_amount, binding, last_line=t.initial_stop)

    for d in dates:
        if prev_date is not None and p.park_apy > 0:
            factor = (1 + p.park_apy) ** ((d - prev_date).days / 365)
            for k in ledger:
                ledger[k] *= factor
        for pos in positions.values():
            pos.last_line = pos.trade.exit_lines.get(d, pos.last_line)

        if p.exits_first:
            process_events(d)
            process_entries(d)
            # 진입한 날 바로 나는 이벤트(구간 마지막 날 진입 + 최종 청산) — 청산 먼저 순서에서는 포지션이 아직 없어 놓친다
            process_events(d, entered_today_only=True)
        else:
            process_entries(d)
            process_events(d)

        di = di_of[d]
        invested = sum(pos.qty * aligned[(pos.trade.mrkt_code, pos.trade.code)][di] for pos in positions.values())
        equity = sum(ledger.values()) + invested
        rows.append((d, equity, invested, len(positions), open_risk() / equity if equity > 0 else 0.0))
        equity_prev, prev_date = equity, d

    curve = pd.DataFrame(rows, columns=["date", "equity", "invested", "n_pos", "open_risk_pct"]).set_index("date")
    signals = sum(len(v) for v in entries.values())
    return SimResult(curve, cycles, skipped, signals, p)


def sim_metrics(res: SimResult) -> Dict:
    """재생 결과 지표 — CAGR·MDD·MAR·샤프·소르티노, 현금 비중, 동시 보유, 한도로 건너뛴 신호"""
    c = res.curve
    if c.empty:
        return {"days": 0}
    e0 = res.params.equity0
    total = c["equity"]
    ret = pd.concat([pd.Series([e0]), total.reset_index(drop=True)]).pct_change().dropna()
    days = max((total.index[-1] - total.index[0]).days, 1)
    cagr = (total.iloc[-1] / e0) ** (365.25 / days) - 1
    peak = pd.concat([pd.Series([e0]), total.reset_index(drop=True)]).cummax().iloc[1:]
    mdd = float(((peak.to_numpy() - total.to_numpy()) / peak.to_numpy()).max())
    downside = ret[ret < 0].std()
    monthly = total.resample("ME").last().pct_change().dropna()
    r = [x["r_net"] for x in res.cycles]
    exposure = float((c["invested"] / total).mean())
    return {
        "final_equity": round(float(total.iloc[-1]), 2),
        "total_return": round(float(total.iloc[-1] / e0 - 1), 4),
        "cagr": round(float(cagr), 4), "mdd": round(mdd, 4),
        "mar": round(float(cagr) / mdd, 2) if mdd > 0 else None,
        "sharpe": round(float(ret.mean() / ret.std() * math.sqrt(252)), 2) if ret.std() > 0 else None,
        "sortino": round(float(ret.mean() / downside * math.sqrt(252)), 2) if downside and downside > 0 else None,
        "monthly_win": round(float((monthly > 0).mean()), 3) if len(monthly) else None,
        "exposure": round(exposure, 3), "cash_ratio": round(1 - exposure, 3),
        "avg_positions": round(float(c["n_pos"].mean()), 1), "max_positions": int(c["n_pos"].max()),
        "open_risk_mean": round(float(c["open_risk_pct"].mean()), 4),
        "open_risk_max": round(float(c["open_risk_pct"].max()), 4),
        "trades": len(r), "signals": res.signals, "skipped": dict(res.skipped),
        "mean_r_net": round(st.mean(r), 3) if r else None,
        "win_rate": round(sum(1 for x in r if x > 0) / len(r), 3) if r else None,
        "cap_binding": sum(1 for x in res.cycles if x["binding"] == risk_sizing.BINDING_POSITION_CAP),
    }


# ==================== 점검용 요약 ====================

def summarize(trades: List[UnitTrade]) -> None:
    for mk in ("KR", "US"):
        ts = [t for t in trades if t.market == mk]
        if not ts:
            continue
        r = [(sum(ratio * (p - t.entry_price) for _, _, p, ratio in t.events)) / (t.entry_price - t.initial_stop)
             for t in ts if t.entry_price > t.initial_stop]
        stops = [(t.entry_price - t.initial_stop) / t.entry_price * 100 for t in ts]
        partial = sum(1 for t in ts if any(k == EVENT_PARTIAL for _, k, _, _ in t.events))
        final = sum(1 for t in ts if any(k == EVENT_FINAL for _, k, _, _ in t.events))
        bad_ratio = sum(1 for t in ts if abs(sum(e[3] for e in t.events) - 1) > 1e-9)
        no_adv = sum(1 for t in ts if pd.isna(t.adv))
        print(f"[{mk}] 종목 {len({t.code for t in ts})}개 · 사이클 {len(ts)}건 · 부분익절 {partial} · 최종청산(미청산) {final}")
        print(f"     손절폭% 평균 {sum(stops)/len(stops):.2f} (최소 {min(stops):.2f}, 최대 {max(stops):.2f})"
              f" · 평균 R {sum(r)/len(r):+.3f} · 승률 {sum(1 for x in r if x > 0)/len(r):.1%}")
        print(f"     점검: 수량 비율 합 ≠ 1 {bad_ratio}건 · ADV 없음 {no_adv}건 · 청산선 기록 없음 "
              f"{sum(1 for t in ts if not t.exit_lines)}건")


# ==================== 판정 (설계 문서 6장, 2026-10-31 확정 — 결과를 보고 바꾸지 않는다) ====================

EQUITY0 = {"KR": 1_000_000_000, "US": 1_000_000}   # 시장별 초기 총자산 (원 / 달러). 유동성 필터·1주 반올림에 영향을 준다
PARK_APY = {"KR": 0.03, "US": 0.04}                # E4 참고용 파킹 수익률 (판정에는 쓰지 않는다)
MAR_RATIO_MIN = 1.2                                # 기준 1: E0 대비 MAR 배수
RANDOM_RUNS = 30                                   # 기준 2: 무작위 우선순위 대조군 시드 수
RANDOM_PERCENTILE = 90                             # 기준 2: 대조군 분포의 이 분위 이상

GRID = {   # E2 격자 — 축 순서가 곧 "인접"의 정의다
    "risk_pct": [0.0025, 0.005, 0.0075, 0.01],
    "max_positions": [10, 20, 30, None],
    "max_position_pct": [0.10, 0.20, 0.30],
    "max_open_risk_pct": [0.04, 0.08, 0.12],
}
HOLDOUT_MARKER = Path(__file__).resolve().parent / ".holdout_done"


def passes_mar_ratio(mar: Optional[float], mar_e0: Optional[float]) -> bool:
    """기준 1 — MAR >= E0의 1.2배. E0 MAR 가 0 이하면 1.2배가 오히려 쉬워지므로 'MAR > 0 이고 E0 보다 높을 것'"""
    if mar is None:
        return False
    if mar_e0 is None or mar_e0 <= 0:
        return mar > 0 and (mar_e0 is None or mar > mar_e0)
    return mar >= MAR_RATIO_MIN * mar_e0


def control_threshold(random_mars: List[Optional[float]]) -> Optional[float]:
    """기준 2 — 무작위 우선순위 대조군 MAR 의 RANDOM_PERCENTILE 분위 (None 은 MAR 없음 = 0 취급)"""
    vals = [m if m is not None else 0.0 for m in random_mars]
    return float(np.percentile(vals, RANDOM_PERCENTILE)) if vals else None


def grid_neighbors(cfg: tuple, grid: Dict[str, list]) -> List[tuple]:
    """기준 3 — 한 축만 한 칸 옮긴 인접 설정 (격자 밖은 제외)"""
    axes = list(grid.values())
    out = []
    for ax, values in enumerate(axes):
        i = values.index(cfg[ax])
        for j in (i - 1, i + 1):
            if 0 <= j < len(values):
                out.append(cfg[:ax] + (values[j],) + cfg[ax + 1:])
    return out


def grid_configs(grid: Dict[str, list]) -> List[tuple]:
    import itertools
    return list(itertools.product(*grid.values()))


def cfg_kwargs(cfg: tuple, grid: Dict[str, list]) -> Dict:
    return dict(zip(grid.keys(), cfg))


# ==================== 실험 실행 ====================

_SHARED: Dict = {}


def prepare(period: str) -> None:
    universe = asyncio.run(load_universe())
    data = tradable(universe)
    trades = extract_all(data, period)
    closes = closes_by_stock(data)
    by_market = {}
    for mk in ("KR", "US"):
        ts = [t for t in trades if t.market == mk]
        keys = {(t.mrkt_code, t.code) for t in ts}
        eligible = sum(1 for (m, _), df in data.items() if market_of(m) == mk and is_eligible(df, period))
        by_market[mk] = {"trades": ts, "closes": {k: v for k, v in closes.items() if k in keys}, "n_stocks": eligible}
    _SHARED.update(markets=by_market, range=PERIODS[period])


def _job(args):
    mk, name, kw = args
    sh = _SHARED["markets"][mk]
    p = ps_params(mk, sh, kw)
    res = simulate(sh["trades"], sh["closes"], p, *_SHARED["range"])
    return mk, name, sim_metrics(res)


def ps_params(mk: str, sh: Dict, kw: Dict) -> SimParams:
    p = SimParams(equity0=EQUITY0[mk], **kw)
    if p.mode == "fixed_alloc":
        p.n_stocks = sh["n_stocks"]
    return p


def run_jobs(jobs: List[tuple]) -> Dict[tuple, Dict]:
    """(시장, 이름, 파라미터) 목록을 fork 병렬로 실행 — 결과는 (시장, 이름) → 지표"""
    import multiprocessing as mp
    with mp.get_context("fork").Pool() as pool:
        return {(mk, name): m for mk, name, m in pool.map(_job, jobs, chunksize=4)}


def fmt(m: Dict) -> str:
    return (f"CAGR {m['cagr']:+.1%} · MDD {m['mdd']:.1%} · MAR {m['mar']} · 샤프 {m['sharpe']} · 노출 {m['exposure']:.0%}"
            f" · 체결 {m['trades']}/{m['signals']} · 건너뜀 {m['skipped']}")


def dev(out: str) -> None:
    import json
    import time
    prepare("dev")
    t0 = time.time()
    for mk in ("KR", "US"):
        n = _SHARED["markets"][mk]
        print(f"[{mk}] 풀 {n['n_stocks']}종목 · 거래 기록 {len(n['trades'])}건 · 초기 자산 {EQUITY0[mk]:,}")

    base_jobs = [(mk, "E0", {"mode": "fixed_alloc"}) for mk in ("KR", "US")]
    base_jobs += [(mk, "E1", {"mode": "pool"}) for mk in ("KR", "US")]
    base = run_jobs(base_jobs)
    print("\n=== 기준선 ===")
    for mk in ("KR", "US"):
        print(f"[{mk}] E0 현재 구조(종목별 배정금)  {fmt(base[(mk, 'E0')])}")
        print(f"[{mk}] E1 풀 공유·1%·한도 없음      {fmt(base[(mk, 'E1')])}")

    configs = grid_configs(GRID)
    grid_jobs = [(mk, cfg, cfg_kwargs(cfg, GRID)) for cfg in configs for mk in ("KR", "US")]
    grid = run_jobs(grid_jobs)
    print(f"\nE2 격자 {len(configs)}개 × 2시장 완료 ({time.time() - t0:.0f}초)")

    mar_e0 = {mk: base[(mk, "E0")]["mar"] for mk in ("KR", "US")}
    crit1 = {cfg: all(passes_mar_ratio(grid[(mk, cfg)]["mar"], mar_e0[mk]) for mk in ("KR", "US")) for cfg in configs}
    passers = [c for c in configs if crit1[c]]
    print(f"기준 1 통과(KR·US 모두 MAR ≥ E0×{MAR_RATIO_MIN}): {len(passers)}/{len(configs)}")

    rand_jobs = [(mk, (cfg, seed), {**cfg_kwargs(cfg, GRID), "priority": "random", "seed": seed})
                 for cfg in passers for seed in range(RANDOM_RUNS) for mk in ("KR", "US")]
    rand = run_jobs(rand_jobs) if rand_jobs else {}
    crit2 = {}
    for cfg in passers:
        ok = True
        for mk in ("KR", "US"):
            thr = control_threshold([rand[(mk, (cfg, s))]["mar"] for s in range(RANDOM_RUNS)])
            ok = ok and thr is not None and (grid[(mk, cfg)]["mar"] or 0.0) >= thr
        crit2[cfg] = ok
    print(f"기준 2 통과(무작위 대조군 {RANDOM_PERCENTILE}분위 이상): {sum(crit2.values())}/{len(passers)}")

    final = []
    for cfg in configs:
        if not (crit1[cfg] and crit2.get(cfg)):
            continue
        if all(crit1[n] for n in grid_neighbors(cfg, GRID)):
            final.append(cfg)
    print(f"기준 3 통과(인접 설정 모두 기준 1 유지) → 최종 후보 {len(final)}개")

    final.sort(key=lambda c: -min(grid[(mk, c)]["mar"] for mk in ("KR", "US")))
    for cfg in final[:10]:
        print(f"\n후보 {cfg_kwargs(cfg, GRID)}")
        for mk in ("KR", "US"):
            print(f"  [{mk}] {fmt(grid[(mk, cfg)])}")

    result = {
        "equity0": EQUITY0, "baseline": {f"{mk}/{n}": base[(mk, n)] for mk, n in base},
        "grid": {f"{cfg}": {mk: grid[(mk, cfg)] for mk in ("KR", "US")} for cfg in configs},
        "crit1": [str(c) for c in passers], "crit2": [str(c) for c, ok in crit2.items() if ok],
        "final": [dict(cfg_kwargs(c, GRID)) for c in final],
    }
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    print(f"\n저장: {out}")


def holdout(config: Dict, force: bool) -> None:
    """홀드아웃(2015~2019) — 최종 후보로 단 1회. 같은 구간으로 E0와 후보를 비교해 기준 1만 확인한다"""
    if HOLDOUT_MARKER.exists() and not force:
        raise SystemExit(f"홀드아웃은 이미 한 번 실행했다 ({HOLDOUT_MARKER}). 결과를 보고 설정을 바꿔 다시 맞추지 않는다.")
    prepare("holdout")
    jobs = [(mk, "E0", {"mode": "fixed_alloc"}) for mk in ("KR", "US")] + [(mk, "CAND", config) for mk in ("KR", "US")]
    res = run_jobs(jobs)
    ok = True
    for mk in ("KR", "US"):
        e0, cand = res[(mk, "E0")], res[(mk, "CAND")]
        passed = passes_mar_ratio(cand["mar"], e0["mar"])
        ok = ok and passed
        print(f"[{mk}] E0   {fmt(e0)}\n[{mk}] 후보 {fmt(cand)}\n[{mk}] 기준 1 {'통과' if passed else '미달'}")
    HOLDOUT_MARKER.write_text(f"{datetime.now().isoformat()} {config} → {'통과' if ok else '미달'}\n")
    print("홀드아웃 결과:", "통과" if ok else "미달")


def main():
    import json
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["extract", "dev", "holdout"])
    ap.add_argument("--period", choices=list(PERIODS), default="dev", help="extract 전용")
    ap.add_argument("--out", default="portfolio_sim_dev.json", help="dev 결과 저장 경로")
    ap.add_argument("--config", help='holdout 후보 설정 JSON, 예: \'{"risk_pct":0.005,"max_positions":20}\'')
    ap.add_argument("--force", action="store_true", help="holdout 재실행 허용 (결과 보고 설정을 맞추지 않을 때만)")
    args = ap.parse_args()

    if args.command == "dev":
        return dev(args.out)
    if args.command == "holdout":
        if not args.config:
            raise SystemExit("--config 가 필요하다")
        return holdout(json.loads(args.config), args.force)

    universe = asyncio.run(load_universe())
    data = tradable(universe)
    print(f"적재: 종목 {len(data)}개 (지수 ETF 제외)")
    summarize(extract_all(data, args.period))


if __name__ == "__main__":
    main()
