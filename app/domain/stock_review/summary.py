"""
종목 리뷰용 데이터 요약 — KIS 원본 응답을 Gemini 프롬프트용 dict로 줄인다.

순수 함수만 둔다 (DB·네트워크·Redis 없음). LLM은 원시 숫자 수백 개를 정확히
읽지 못하므로 지표를 여기서 계산해 넘기고, 오독하기 쉬운 값(연 누적 분기 실적,
진행 중인 연도의 연간 행, KIS의 결측 표시 99.99)은 여기서 정리한다.
"""
import re
from datetime import date
from typing import Optional

import numpy as np
import talib as ta

# KIS 재무 TR이 값이 없는 항목에 채워 보내는 표시
_MISSING = "99.99"

# 주식현재가 iscd_stat_cls_code
_STAT_MANAGED = "51"       # 관리종목
_STAT_INVEST_RISK = "52"   # 투자위험
_STAT_INVEST_WARN = "53"   # 투자경고
_STAT_INVEST_CAUTION = "54"  # 투자주의
_STAT_HALTED = "58"        # 거래정지
_STAT_OVERHEATED = "59"    # 단기과열

# 주식현재가 mrkt_warn_cls_code
_WARN_CAUTION = "01"
_WARN_WARNING = "02"
_WARN_RISK = "03"

# 종목 하나가 아니라 시장 전체를 훑는 묶음 기사 — 종목코드가 붙어 와도 정보가 없다
_NEWS_NOISE = re.compile(
    r"증시요약|상위종목|상위\s*\d+\s*종목|마감시황|특징\s*(종목|상한가)|"
    r"현재\s*코스(피|닥)|매수우위|매도우위|(오전|오후)장\s*특징주"
)

# KIS 뉴스 제목에서 가운뎃점(·)이 SUB(0x1A)로 깨져 온다 ("금융위\x1a금감원")
_BROKEN_MIDDLE_DOT = "\x1a"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

EMA_PERIOD = 20  # 실전 전략(SingleEMAStrategy)의 주 지표와 같은 기간


def _num(value) -> Optional[float]:
    """KIS 문자열 숫자 → float. 빈 값·결측 표시는 None"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == _MISSING:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return round(float(value), digits)


def _pct(now: Optional[float], base: Optional[float]) -> Optional[float]:
    if now is None or not base:
        return None
    return _round((now - base) / base * 100)


# ============================================================
# 종목 상태 (사전 차단 / 경고)
# ============================================================

def blocking_reasons(quote: dict) -> list[str]:
    """스윙 등록 자체가 부적합한 상태 — 하나라도 있으면 Gemini를 부르지 않는다"""
    stat = quote.get("iscd_stat_cls_code")
    warn = quote.get("mrkt_warn_cls_code")
    reasons = []
    if stat == _STAT_MANAGED or quote.get("mang_issu_cls_code") == "Y":
        reasons.append("관리종목")
    if stat == _STAT_INVEST_RISK or warn == _WARN_RISK:
        reasons.append("투자위험종목")
    if stat == _STAT_HALTED or quote.get("temp_stop_yn") == "Y":
        reasons.append("거래정지")
    if quote.get("sltr_yn") == "Y":
        reasons.append("정리매매")
    return reasons


def warning_flags(quote: dict) -> list[str]:
    """등록은 가능하지만 판단에 반영해야 할 시장 경고"""
    stat = quote.get("iscd_stat_cls_code")
    warn = quote.get("mrkt_warn_cls_code")
    flags = []
    if stat == _STAT_INVEST_WARN or warn == _WARN_WARNING:
        flags.append("투자경고")
    if stat == _STAT_INVEST_CAUTION or warn == _WARN_CAUTION:
        flags.append("투자주의")
    if stat == _STAT_OVERHEATED:
        flags.append("단기과열")
    if quote.get("short_over_yn") == "Y":
        flags.append("공매도 과열")
    if quote.get("invt_caful_yn") == "Y":
        flags.append("투자유의")
    return flags


# ============================================================
# 현재가 / 밸류에이션
# ============================================================

def summarize_quote(quote: dict) -> dict:
    """주식현재가 시세 output → 밸류에이션·위치 요약"""
    return {
        "업종": quote.get("bstp_kor_isnm"),
        "대표시장": quote.get("rprs_mrkt_kor_name"),
        "현재가": _num(quote.get("stck_prpr")),
        "전일대비_pct": _num(quote.get("prdy_ctrt")),
        "시가총액_억원": _num(quote.get("hts_avls")),
        # 현재가 TR의 PER/EPS 등은 직전 결산(연간) 기준 — 재무비율의 연 누적 EPS 와 섞어 읽지 않게 이름에 기준을 붙인다
        "PER_직전결산기준": _num(quote.get("per")),
        "PBR_직전결산기준": _num(quote.get("pbr")),
        "EPS_직전결산": _num(quote.get("eps")),
        "BPS_직전결산": _num(quote.get("bps")),
        "외국인보유율_pct": _num(quote.get("hts_frgn_ehrt")),
        "신용잔고율_pct": _num(quote.get("whol_loan_rmnd_rate")),
        "결산월": quote.get("stac_month"),
        "250일최고_대비_pct": _num(quote.get("d250_hgpr_vrss_prpr_rate")),
        "250일최저_대비_pct": _num(quote.get("d250_lwpr_vrss_prpr_rate")),
        "시장경고": warning_flags(quote),
    }


# ============================================================
# 일봉 / 기술 지표
# ============================================================

def _bar_arrays(bars: list[dict]):
    rows = sorted(bars, key=lambda b: str(b["STCK_BSOP_DATE"]))
    dates = [str(b["STCK_BSOP_DATE"]) for b in rows]
    o, h, l, c, v = (
        np.array([float(b[k]) for b in rows], dtype=float)
        for k in ("STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "STCK_CLPR", "ACML_VOL")
    )
    return dates, o, h, l, c, v


def _last(arr) -> Optional[float]:
    if arr is None or len(arr) == 0:
        return None
    return _round(arr[-1])


def _streak_vs(close: np.ndarray, line: np.ndarray) -> int:
    """기준선 위(+)/아래(-) 연속 일수. NaN 구간에서 멈춘다"""
    if len(close) == 0 or np.isnan(line[-1]):
        return 0
    above = close[-1] >= line[-1]
    n = 0
    for c, e in zip(close[::-1], line[::-1]):
        if np.isnan(e) or (c >= e) != above:
            break
        n += 1
    return n if above else -n


def summarize_price(bars: list[dict], last_bar_incomplete: bool = False) -> dict:
    """6개월 일봉 → 추세·모멘텀·변동성·수급 지표 요약

    Args:
        bars: STCK_BSOP_DATE/STCK_OPRC/STCK_HGPR/STCK_LWPR/STCK_CLPR/ACML_VOL 행 (순서 무관)
        last_bar_incomplete: 마지막 봉이 장중 미완성인지 (지표에도 그 값이 반영됨)
    """
    if not bars:
        return {"봉수": 0}

    dates, _, high, low, close, vol = _bar_arrays(bars)
    n = len(close)
    last = close[-1]

    def ret(days: int) -> Optional[float]:
        return _pct(last, close[-days - 1]) if n > days else None

    ema20 = ta.EMA(close, timeperiod=EMA_PERIOD) if n >= EMA_PERIOD else np.full(n, np.nan)
    ema60 = ta.EMA(close, timeperiod=60) if n >= 60 else np.full(n, np.nan)
    rsi = ta.RSI(close, timeperiod=14) if n > 14 else None
    adx = ta.ADX(high, low, close, timeperiod=14) if n >= 28 else None
    plus_di = ta.PLUS_DI(high, low, close, timeperiod=14) if n > 14 else None
    minus_di = ta.MINUS_DI(high, low, close, timeperiod=14) if n > 14 else None
    atr = ta.ATR(high, low, close, timeperiod=14) if n > 14 else None
    obv = ta.OBV(close, vol)

    hi6, lo6 = float(high.max()), float(low.min())
    # 거래량 배수는 마지막 봉을 뺀 직전 20일 평균과 비교 (미완성 봉이 평균을 깎지 않게)
    vol_base = vol[-21:-1] if n > 20 else vol[:-1]
    avg_vol20 = float(vol_base.mean()) if len(vol_base) else None

    recent = [
        {
            "일자": dates[i],
            "종가": _round(close[i]),
            "등락_pct": _pct(close[i], close[i - 1]) if i > 0 else None,
            "거래량": int(vol[i]),
        }
        for i in range(max(0, n - 20), n)
    ]

    return {
        "봉수": n,
        "기간": f"{dates[0]}~{dates[-1]}",
        "마지막봉_장중미완성": last_bar_incomplete,
        "종가": _round(last),
        "수익률_pct": {"1개월": ret(21), "3개월": ret(63), "6개월": _pct(last, close[0])},
        "6개월_최고가": _round(hi6),
        "6개월_최저가": _round(lo6),
        # 0 = 6개월 최저, 100 = 6개월 최고
        "6개월범위_위치_pct": _round((last - lo6) / (hi6 - lo6) * 100) if hi6 > lo6 else None,
        "EMA20": _last(ema20),
        "EMA20_괴리_pct": _pct(last, ema20[-1]) if not np.isnan(ema20[-1]) else None,
        "EMA20_5일기울기_pct": (
            _pct(ema20[-1], ema20[-6]) if n >= EMA_PERIOD + 5 and not np.isnan(ema20[-6]) else None
        ),
        "EMA20_위아래_연속일수": _streak_vs(close, ema20),
        "EMA60_괴리_pct": _pct(last, ema60[-1]) if not np.isnan(ema60[-1]) else None,
        "RSI14": _last(rsi),
        "ADX14": _last(adx),
        "+DI": _last(plus_di),
        "-DI": _last(minus_di),
        "ATR14_종가대비_pct": _round(atr[-1] / last * 100) if atr is not None and last else None,
        # OBV 20일 변화 방향과 가격 20일 변화 방향 — 어긋나면 수급 다이버전스
        "OBV_20일변화": _round(obv[-1] - obv[-21]) if n > 20 else None,
        "가격_20일변화_pct": ret(20),
        "거래량_20일평균대비_배": _round(vol[-1] / avg_vol20) if avg_vol20 else None,
        "최근20봉": recent,
    }


# ============================================================
# 재무
# ============================================================

def _yymm_minus_3(yymm: str) -> str:
    year, month = int(yymm[:4]), int(yymm[4:])
    month -= 3
    if month <= 0:
        year, month = year - 1, month + 12
    return f"{year:04d}{month:02d}"


def _first_quarter_month(fiscal_end_month: int) -> int:
    """회계연도 첫 분기 결산월 (12월 결산 → 3월, 6월 결산 → 9월, 10월 결산 → 1월)"""
    return (fiscal_end_month + 3 - 1) % 12 + 1


_INCOME_FIELDS = {"sale_account": "매출액", "bsop_prti": "영업이익", "thtr_ntin": "당기순이익"}


def quarterly_income(rows: list[dict], fiscal_end_month: int, limit: int = 5) -> list[dict]:
    """연 누적 분기 손익 → 개별 분기 값 (최신 먼저)

    KIS 분기 손익은 회계연도 시작부터의 누적이다 (12월 결산 202606 = 1~6월 합계).
    첫 분기는 누적값이 곧 분기값이고, 나머지는 직전 분기 누적을 뺀다.
    직전 분기 행이 없으면 계산할 수 없으므로 None으로 둔다.
    """
    by_yymm = {r["stac_yymm"]: r for r in rows if r.get("stac_yymm")}
    first_q = _first_quarter_month(fiscal_end_month)
    result = []
    for yymm in sorted(by_yymm, reverse=True)[:limit]:
        cur = by_yymm[yymm]
        prev = None if int(yymm[4:]) == first_q else by_yymm.get(_yymm_minus_3(yymm))
        item = {"결산월": yymm}
        for field, label in _INCOME_FIELDS.items():
            value = _num(cur.get(field))
            if int(yymm[4:]) != first_q:
                prev_value = _num(prev.get(field)) if prev else None
                value = value - prev_value if value is not None and prev_value is not None else None
            item[f"{label}_억원"] = _round(value)
        result.append(item)
    return result


def annual_income(rows: list[dict], fiscal_end_month: int, limit: int = 3) -> list[dict]:
    """연간 손익 — 결산월 행만 (진행 중인 연도의 누적 행은 확정 실적이 아니므로 제외)"""
    closed = [r for r in rows if r.get("stac_yymm") and int(r["stac_yymm"][4:]) == fiscal_end_month]
    closed.sort(key=lambda r: r["stac_yymm"], reverse=True)
    return [
        {"결산월": r["stac_yymm"], **{f"{label}_억원": _num(r.get(f)) for f, label in _INCOME_FIELDS.items()}}
        for r in closed[:limit]
    ]


def ratio_rows(ratio: list[dict], stability: list[dict], limit: int = 5) -> list[dict]:
    """재무비율 + 안정성비율 (최신 먼저). 분기 행은 연 누적 기준 비율이다"""
    stab_by = {r.get("stac_yymm"): r for r in stability}
    rows = sorted((r for r in ratio if r.get("stac_yymm")), key=lambda r: r["stac_yymm"], reverse=True)
    return [
        {
            "결산월": r["stac_yymm"],
            "ROE_pct": _num(r.get("roe_val")),
            "매출액증가율_pct": _num(r.get("grs")),
            "영업이익증가율_pct": _num(r.get("bsop_prfi_inrt")),
            "순이익증가율_pct": _num(r.get("ntin_inrt")),
            "부채비율_pct": _num(r.get("lblt_rate")),
            "유동비율_pct": _num((stab_by.get(r["stac_yymm"]) or {}).get("crnt_rate")),
            "EPS_연누적": _num(r.get("eps")),   # 해당 결산월까지 올해 누적 (연간 EPS 와 직접 비교 불가)
            "BPS_분기말": _num(r.get("bps")),
        }
        for r in rows[:limit]
    ]


def summarize_financials(income_q: list[dict], income_y: list[dict], ratio_q: list[dict],
                         stability_q: list[dict], fiscal_end_month: int) -> dict:
    return {
        "분기실적_개별분기": quarterly_income(income_q, fiscal_end_month),
        "연간실적_확정": annual_income(income_y, fiscal_end_month),
        "재무비율_분기_연누적기준": ratio_rows(ratio_q, stability_q),
    }


# ============================================================
# 투자자 동향
# ============================================================

_INVESTORS = {"frgn": "외국인", "orgn": "기관", "prsn": "개인"}


def summarize_investors(rows: list[dict], first_row_incomplete: bool = False) -> dict:
    """투자자별 순매수 금액(백만원) — 5일/20일 누적 + 최근 10일 (최신 먼저)"""
    rows = sorted((r for r in rows if r.get("stck_bsop_date")), key=lambda r: r["stck_bsop_date"], reverse=True)

    def total(key: str, days: int) -> Optional[float]:
        values = [_num(r.get(f"{key}_ntby_tr_pbmn")) for r in rows[:days]]
        values = [v for v in values if v is not None]
        return _round(sum(values)) if values else None

    return {
        "단위": "백만원",
        "최신일_장중집계": first_row_incomplete,
        "누적순매수": {
            f"{label}_{days}일": total(key, days)
            for key, label in _INVESTORS.items() for days in (5, 20)
        },
        "최근10일": [
            {"일자": r["stck_bsop_date"],
             **{label: _num(r.get(f"{key}_ntby_tr_pbmn")) for key, label in _INVESTORS.items()}}
            for r in rows[:10]
        ],
    }


# ============================================================
# 뉴스 / 공시
# ============================================================

_WEEKDAYS = "월화수목금토일"


def _news_date(yyyymmdd: Optional[str]) -> Optional[str]:
    """20261020 → '2026-10-20 (화)' — 모델이 날짜·요일을 바로 읽도록"""
    if not yyyymmdd or len(yyyymmdd) != 8:
        return yyyymmdd
    try:
        d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    except ValueError:
        return yyyymmdd
    return f"{d.isoformat()} ({_WEEKDAYS[d.weekday()]})"


def _news_time(hhmmss: Optional[str]) -> Optional[str]:
    return f"{hhmmss[:2]}:{hhmmss[2:4]}" if hhmmss and len(hhmmss) >= 4 else hhmmss


def _is_disclosure(row: dict) -> bool:
    """공시 (출처가 '코스닥 공시' 등) — 하루 안에서 일반 기사보다 먼저 담는다"""
    return "공시" in (row.get("dorg") or "")


def filter_news(rows: list[dict], code: str, name: str = "",
                stock_limit: int = 30, market_limit: int = 10,
                stock_per_day: int = 5, market_per_day: int = 2) -> dict:
    """뉴스·공시 제목을 종목 기사 / 시장·업종 기사로 나눠 날짜별로 고르게 담는다 (최신 날짜 먼저)

    news-title TR은 FID_INPUT_ISCD로 관련 기사를 제대로 골라 주지만, 응답의
    iscd1~10 에는 관련 종목 일부만 실린다 — "에코프로비엠, 유상증자 청약률…"이
    086520(에코프로)만 달고 오는 식이다. 그래서 응답 종목코드만으로 거르면
    핵심 기사를 버린다. 코드가 붙었거나 제목에 종목명이 있으면 종목 기사,
    나머지는 KIS가 관련으로 분류한 시장·업종 기사로 따로 넘긴다.

    기사가 많은 종목(삼성전자 등)은 하루에만 40건이 넘어 최신순으로 자르면 오늘 기사만 남고
    며칠 전 실적·공시가 빠진다. 그래서 하루 상한(stock_per_day)을 두어 최근 며칠에 고르게 나누고,
    하루 안에서는 공시를 먼저 담는다.
    """
    seen = set()
    stock_by_day: dict[str, list] = {}
    market_by_day: dict[str, list] = {}
    # 날짜 최신순, 같은 날은 공시 먼저 → 시각 최신순
    ordered = sorted(
        rows,
        key=lambda r: (r.get("data_dt", ""), _is_disclosure(r), r.get("data_tm", "")),
        reverse=True,
    )
    for r in ordered:
        title = (r.get("hts_pbnt_titl_cntt") or "").replace(_BROKEN_MIDDLE_DOT, "·")
        title = _CONTROL_CHARS.sub("", title).strip()
        if not title or _NEWS_NOISE.search(title):
            continue
        key = re.sub(r"\s+", "", title)
        if key in seen:
            continue
        seen.add(key)

        day = r.get("data_dt") or ""
        item = {"일자": _news_date(day), "시각": _news_time(r.get("data_tm")),
                "출처": r.get("dorg"), "제목": title}
        tagged = code in {r.get(f"iscd{i}") for i in range(1, 11)}
        if tagged or (name and name in title):
            bucket, cap = stock_by_day.setdefault(day, []), stock_per_day
        else:
            bucket, cap = market_by_day.setdefault(day, []), market_per_day
        if len(bucket) < cap:
            bucket.append(item)

    def flatten(by_day: dict, limit: int) -> list:
        result = []
        for day in sorted(by_day, reverse=True):
            result.extend(by_day[day])
        return result[:limit]

    return {"종목": flatten(stock_by_day, stock_limit), "시장_업종": flatten(market_by_day, market_limit)}
