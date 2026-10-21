"""
Stock Review Service — 스윙 등록 전 종목 판단 보조

KIS에서 시세·재무·수급·뉴스를 모아 요약하고 Gemini에 판단을 맡긴다.
무료 등급 Gemini는 응답에 1분 넘게 걸리므로 백그라운드 작업으로 돌리고,
사용자는 목록(헤더 아이콘)으로 진행 상태를 확인한다.

DB에는 쓰지 않는다: 등록 전 종목의 일봉을 STOCK_DAY_HISTORY에 넣으면
day_collect_job 이 그 종목을 영구히 수집하게 되므로(swing/service.py
_ensure_stock_data 참고) 조회만 하고 상태·결과는 Redis에 둔다.

Redis 키
- stock_review:{mrkt}:{code}          결과 캐시 (사용자 공유, 장중 30분 / 장 밖 다음 평일 개장까지)
- stock_review:running:{mrkt}:{code}  분석 중 표시 (값 = 작업 토큰, SET NX — 같은 종목 중복 실행 방지)
- stock_review:waiters:{mrkt}:{code}  결과를 기다리는 사용자 집합
- stock_review:user:{user_id}         사용자별 분석 목록 (hash, 종목코드 → 상태·결과 JSON)
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, Union
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.database import Database
from app.common.redis import get_redis
from app.domain.stock.repository import StockRepository
from app.domain.stock.stock_data_batch import _SESSION_COMPLETE, is_today_incomplete
from app.domain.stock_review import summary
from app.domain.stock_review.prompt import (
    build_analysis_instruction, build_prompt, build_strategy_instruction, build_strategy_prompt,
)
from app.domain.stock_review.schemas import GeminiReview, StockReviewResponse, strategy_pick_schema
from app.domain.swing.trading.trading_strategy_factory import TradingStrategyFactory
from app.core.market_code import is_overseas
from app.exceptions import AppError, NotFoundError
from app.external import finnhub_api, foreign_api, gemini_api, kis_api

logger = logging.getLogger(__name__)

DEFAULT_MRKT = "J"
_KST = ZoneInfo("Asia/Seoul")

PRICE_DAYS = 185          # 약 6개월 (거래일 ~120)
_PRICE_CHUNK_DAYS = 92    # KIS 일봉 TR은 1회 최대 100봉 → 3개월씩 두 번
NEWS_DAYS = 7
_NEWS_MAX_CALLS = 7
# 미국: 해외 뉴스 TR은 1회 10건이고 기사가 드문 종목이 많아 기간을 늘린다
# (기사가 많은 종목은 최근 날짜로 상한이 차고, 드문 종목만 더 오래된 기사까지 담긴다 — 날짜는 모델이 본다)
US_NEWS_DAYS = 30
_US_NEWS_MAX_CALLS = 5
_US_BAR_CALLS = 2         # 해외 일봉 TR도 1회 100봉 (BYMD 부터 과거로)

_RESULT_KEY = "stock_review:{mrkt}:{code}"
_RUNNING_KEY = "stock_review:running:{mrkt}:{code}"
_WAITERS_KEY = "stock_review:waiters:{mrkt}:{code}"
_USER_KEY = "stock_review:user:{user_id}"

# 최악의 작업 시간(모델 4개 × 2회 × 호출 타임아웃 3분 + 재시도 대기)보다 길게 둔다.
# 이보다 오래 RUNNING 이면 작업이 죽은 것(서버 재시작 등)으로 보고 다시 요청할 수 있게 한다.
_RUNNING_TTL = 30 * 60
_USER_TTL = 24 * 60 * 60
_INTRADAY_TTL = 30 * 60
# 시장별 정규장 시작과 당일 봉 완성 시각 (완성 시각은 일봉 적재 배치와 같은 기준)
_SESSIONS = {
    "KR": {"tz": _KST, "open": dt_time(9, 0), "end": _SESSION_COMPLETE["KR"]["time"]},
    "US": {"tz": ZoneInfo(_SESSION_COMPLETE["US"]["tz"]), "open": dt_time(9, 30), "end": _SESSION_COMPLETE["US"]["time"]},
}


def _market(mrkt: str) -> str:
    return "US" if is_overseas(mrkt) else "KR"

STATUS_RUNNING = "RUNNING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"

# 분석 중 표시를 '내가 건 것일 때만' 지운다. 작업이 TTL 을 넘겨 다른 작업이 표시를 새로
# 걸었다면, 늦게 끝난 쪽이 남의 표시·대기자를 지우면 안 된다 (확인과 삭제를 원자적으로)
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1], KEYS[2])
end
return 0
"""

# create_task 결과를 붙잡아 둔다 — 참조가 없으면 실행 중에 GC될 수 있다
_jobs: set[asyncio.Task] = set()


def _cache_ttl(now: datetime, mrkt: str = DEFAULT_MRKT) -> int:
    """장중엔 30분, 그 외엔 그 시장의 다음 평일 개장까지 — 장 밖에서는 시세·수급이 바뀌지 않는다

    시각은 시장 현지 시간으로 판단한다 (미국은 뉴욕). 공휴일은 판별하지 않는다
    (auto_swing_batch.is_market_open 과 같은 한계).
    """
    session = _SESSIONS[_market(mrkt)]
    local = now.astimezone(session["tz"])
    if local.weekday() < 5 and session["open"] <= local.time() < session["end"]:
        return _INTRADAY_TTL
    next_day = local.date() if local.weekday() < 5 and local.time() < session["open"] else local.date() + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    next_open = datetime.combine(next_day, session["open"], tzinfo=session["tz"])
    return max(int((next_open - local).total_seconds()), 60)


def _now() -> datetime:
    return datetime.now(_KST)


def _summary_of(entry: dict) -> dict:
    """목록 응답용 — 결과 본문은 빼고 상태만"""
    result = entry.get("RESULT") or {}
    return {
        "MRKT_CODE": entry.get("MRKT_CODE", DEFAULT_MRKT),
        "ST_CODE": entry["ST_CODE"],
        "ST_NM": entry.get("ST_NM"),
        "STATUS": entry["STATUS"],
        "VERDICT": result.get("VERDICT"),
        "ERROR": entry.get("ERROR"),
        # QUOTA_EXHAUSTED 면 RETRY_AT 전까지 다시 요청해도 실패한다 — 앱이 재시도를 막고 시각을 안내
        "ERROR_CODE": entry.get("ERROR_CODE"),
        "RETRY_AT": entry.get("RETRY_AT"),
        "SEEN": entry.get("SEEN", False),
        "REQUESTED_AT": entry.get("REQUESTED_AT"),
        "FINISHED_AT": entry.get("FINISHED_AT"),
    }


def _keys(mrkt: str, st_code: str) -> dict:
    return {
        "result": _RESULT_KEY.format(mrkt=mrkt, code=st_code),
        "running": _RUNNING_KEY.format(mrkt=mrkt, code=st_code),
        "waiters": _WAITERS_KEY.format(mrkt=mrkt, code=st_code),
    }


async def _save_entry(redis, user_id: str, entry: dict) -> None:
    key = _USER_KEY.format(user_id=user_id)
    await redis.hset(key, entry["ST_CODE"], json.dumps(entry, ensure_ascii=False))
    await redis.expire(key, _USER_TTL)


async def _release(redis, mrkt: str, st_code: str, token: str) -> None:
    keys = _keys(mrkt, st_code)
    await redis.eval(_RELEASE_SCRIPT, 2, keys["running"], keys["waiters"], token)


@dataclass
class CollectedData:
    """Gemini에 넘길 준비가 끝난 수집 결과 (DB 세션 없이 판단 단계로 넘긴다)"""
    mrkt: str
    st_code: str
    st_nm: str
    now: datetime
    as_of: str              # 프롬프트에 넣을 조회 시각 (미국은 뉴욕 시각 병기)
    data: dict
    last_date: Optional[str]
    bar_incomplete: bool


class StockReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.stock_repo = StockRepository(db)

    # ============================================================
    # 요청 / 조회
    # ============================================================

    async def request_review(self, user_id: str, st_code: str, mrkt: str = DEFAULT_MRKT) -> dict:
        """분석 요청 — 캐시가 있으면 즉시 완료, 없으면 백그라운드 작업을 시작(또는 합류)한다

        mrkt: J(국내) / NYS·NAS·AMS(미국)
        """
        stock = await self.stock_repo.find_by_code(mrkt, st_code)
        if not stock:
            raise NotFoundError("종목", st_code)
        st_nm = stock["ST_NM"]

        redis = await get_redis()
        keys = _keys(mrkt, st_code)
        entry = {"MRKT_CODE": mrkt, "ST_CODE": st_code, "ST_NM": st_nm,
                 "REQUESTED_AT": _now().isoformat(timespec="seconds")}

        cached = await redis.get(keys["result"])
        if cached:
            return await self._mark_done(redis, user_id, entry, cached)

        # AI 사용량을 다 쓴 상태면 시작하지 않는다 — 시작해도 KIS 조회만 하고 실패한다
        reset = gemini_api.quota_reset_at()
        if reset:
            raise gemini_api.quota_exhausted_error(reset)

        entry.update(STATUS=STATUS_RUNNING)
        await _save_entry(redis, user_id, entry)
        await redis.sadd(keys["waiters"], user_id)
        await redis.expire(keys["waiters"], _RUNNING_TTL)

        # 이미 다른 요청이 분석 중이면 대기자로만 합류한다
        token = uuid.uuid4().hex
        started = await redis.set(keys["running"], token, nx=True, ex=_RUNNING_TTL)

        # 위 캐시 확인과 대기자 등록 사이에 작업이 끝났을 수 있다 — 그 작업은 대기자를
        # 읽은 뒤라 나를 모른다. 다시 확인해 결과가 있으면 바로 완료로 받는다.
        cached = await redis.get(keys["result"])
        if cached:
            if started:
                await _release(redis, mrkt, st_code, token)  # 방금 끝난 결과가 있으니 다시 돌릴 필요 없음
            return await self._mark_done(redis, user_id, entry, cached)

        if started:
            task = asyncio.create_task(_run_job(user_id, mrkt, st_code, st_nm, token))
            _jobs.add(task)
            task.add_done_callback(_jobs.discard)
            logger.info(f"[종목리뷰] {st_code} {st_nm} 분석 시작 (user={user_id})")
        return _summary_of(entry)

    async def list_reviews(self, user_id: str) -> list[dict]:
        """내 분석 목록 (최근 요청 먼저)"""
        redis = await get_redis()
        raw = await redis.hgetall(_USER_KEY.format(user_id=user_id))
        entries = [await self._settle(redis, user_id, json.loads(v)) for v in raw.values()]
        entries.sort(key=lambda e: e.get("REQUESTED_AT") or "", reverse=True)
        return [_summary_of(e) for e in entries]

    async def get_review(self, user_id: str, st_code: str) -> dict:
        """분석 상태·결과 조회. 완료된 결과를 조회하면 확인한 것으로 표시한다"""
        redis = await get_redis()
        raw = await redis.hget(_USER_KEY.format(user_id=user_id), st_code)
        if not raw:
            raise NotFoundError("종목 분석", st_code)
        entry = await self._settle(redis, user_id, json.loads(raw))
        if entry["STATUS"] == STATUS_DONE and not entry.get("SEEN"):
            entry["SEEN"] = True
            await _save_entry(redis, user_id, entry)
        return {**_summary_of(entry), "RESULT": entry.get("RESULT")}

    @staticmethod
    async def _mark_done(redis, user_id: str, entry: dict, cached: str) -> dict:
        entry.update(STATUS=STATUS_DONE, RESULT=json.loads(cached), SEEN=False, ERROR=None,
                     ERROR_CODE=None, RETRY_AT=None, FINISHED_AT=_now().isoformat(timespec="seconds"))
        await _save_entry(redis, user_id, entry)
        return _summary_of(entry)

    @staticmethod
    async def _settle(redis, user_id: str, entry: dict) -> dict:
        """RUNNING 인데 분석 중 표시가 사라졌으면 작업이 중단된 것 (서버 재시작 등)"""
        running_key = _keys(entry.get("MRKT_CODE", DEFAULT_MRKT), entry["ST_CODE"])["running"]
        if entry["STATUS"] == STATUS_RUNNING and not await redis.exists(running_key):
            entry.update(STATUS=STATUS_FAILED, ERROR="분석이 중단되었습니다. 다시 시도해주세요",
                         FINISHED_AT=_now().isoformat(timespec="seconds"))
            await _save_entry(redis, user_id, entry)
        return entry

    # ============================================================
    # 분석 실행 (백그라운드) — 수집(DB 사용)과 판단(DB 미사용)을 나눈다
    # ============================================================

    async def collect(self, user_id: str, mrkt: str, st_code: str, st_nm: str) -> Union[StockReviewResponse, CollectedData]:
        """데이터 수집·요약. 관리종목·거래정지 등 차단 대상이면 그 자리에서 최종 결과를 돌려준다

        인증은 사용자가 지금 쓰는 키로 한 번만 고른다 (TR마다 다시 고르면 매번 DB를 조회한다).
        """
        auth = await kis_api.get_session_quote_auth(user_id, self.db)
        if is_overseas(mrkt):
            return await self._collect_us(user_id, mrkt, st_code, st_nm, auth)
        return await self._collect_kr(user_id, mrkt, st_code, st_nm, auth)

    @staticmethod
    def _blocked(mrkt: str, st_code: str, st_nm: str, reasons: list[str], now: datetime) -> StockReviewResponse:
        return StockReviewResponse(
            MRKT_CODE=mrkt, ST_CODE=st_code, ST_NM=st_nm,
            VERDICT="부적합",
            SUMMARY=f"{', '.join(reasons)} 종목이라 투자 대상에서 제외합니다.",
            BLOCKED_REASONS=reasons,
            REVIEWED_AT=now.isoformat(timespec="seconds"),
        )

    async def _collect_kr(self, user_id: str, mrkt: str, st_code: str, st_nm: str,
                          auth: dict) -> Union[StockReviewResponse, CollectedData]:
        """국내 — KIS 현재가·일봉·투자자·재무·뉴스"""
        now = _now()
        quote = (await kis_api.get_inquire_price(user_id, st_code, self.db, access_data=auth))["output"]

        blocked = summary.blocking_reasons(quote)
        if blocked:
            return self._blocked(mrkt, st_code, st_nm, [f"{r} 지정" for r in blocked], now)

        today = now.strftime("%Y%m%d")
        session_open = is_today_incomplete(mrkt)

        bars = await self._fetch_bars(user_id, st_code, now, auth)
        last_date = max((b["STCK_BSOP_DATE"] for b in bars), default=None)
        bar_incomplete = session_open and last_date == today

        investors = await kis_api.get_investor_trend(user_id, st_code, self.db, access_data=auth)
        investor_incomplete = session_open and bool(investors) and investors[0].get("stck_bsop_date") == today

        async def fin(kind: str, quarterly: bool = True) -> list:
            return await kis_api.get_financial_statement(
                user_id, st_code, kind, self.db, quarterly=quarterly, access_data=auth,
            )

        financials = summary.summarize_financials(
            income_q=await fin("income"),
            income_y=await fin("income", quarterly=False),
            ratio_q=await fin("ratio"),
            stability_q=await fin("stability"),
            fiscal_end_month=int(quote.get("stac_month") or 12),
        )
        news = summary.filter_news(await self._fetch_news(user_id, st_code, now, auth), st_code, name=st_nm)

        return CollectedData(
            mrkt=mrkt, st_code=st_code, st_nm=st_nm, now=now, as_of=f"{now:%Y-%m-%d %H:%M} KST",
            last_date=last_date, bar_incomplete=bar_incomplete,
            data={
                "현재가_밸류에이션": summary.summarize_quote(quote),
                "일봉_지표": summary.summarize_price(bars, last_bar_incomplete=bar_incomplete),
                "재무": financials,
                "투자자_순매수": summary.summarize_investors(investors, first_row_incomplete=investor_incomplete),
                "뉴스": news,
            },
        )

    async def _collect_us(self, user_id: str, mrkt: str, st_code: str, st_nm: str,
                          auth: dict) -> Union[StockReviewResponse, CollectedData]:
        """미국 — KIS 해외 현재가·일봉·뉴스 + Finnhub 재무 (투자자별 순매수는 미국에 없다)"""
        now = _now()
        local_now = now.astimezone(_SESSIONS["US"]["tz"])
        quote = await foreign_api.get_inquire_price(user_id, st_code, self.db, excd=mrkt, access_data=auth) or {}

        blocked = summary.us_blocking_reasons(quote)
        if blocked:
            return self._blocked(mrkt, st_code, st_nm, blocked, now)

        bars = await self._fetch_us_bars(user_id, mrkt, st_code, local_now, auth)
        today = local_now.strftime("%Y%m%d")
        # 정규장 시작 전에도 KIS 해외 일봉은 프리마켓 값으로 당일 봉을 준다. 거래량이 거의 없어
        # 거래량 배수·OBV 를 왜곡하므로 뺀다 (STOCK_DAY_HISTORY 프리마켓 봉 오염과 같은 문제)
        if local_now.time() < _SESSIONS["US"]["open"]:
            bars = [b for b in bars if b["STCK_BSOP_DATE"] != today]
        last_date = max((b["STCK_BSOP_DATE"] for b in bars), default=None)
        bar_incomplete = is_today_incomplete(mrkt) and last_date == today

        news = summary.filter_us_news(await self._fetch_us_news(user_id, mrkt, st_code, now, auth))

        return CollectedData(
            mrkt=mrkt, st_code=st_code, st_nm=st_nm, now=now,
            as_of=f"{now:%Y-%m-%d %H:%M} KST (뉴욕 {local_now:%Y-%m-%d %H:%M})",
            last_date=last_date, bar_incomplete=bar_incomplete,
            data={
                "현재가_밸류에이션": summary.summarize_us_quote(quote),
                "일봉_지표": summary.summarize_price(bars, last_bar_incomplete=bar_incomplete),
                "재무": await self._us_financials(st_code),
                "뉴스": news,
            },
        )

    @staticmethod
    async def _us_financials(symbol: str) -> dict:
        """Finnhub 재무 — 실패해도 분석은 계속한다 (일부 종목은 데이터가 없다).
        실패 사실을 데이터에 남겨 모델이 '재무 근거 없음'을 감안하게 한다."""
        try:
            return summary.summarize_us_financials(
                await finnhub_api.get_metrics(symbol), await finnhub_api.get_earnings(symbol),
            )
        except AppError as e:
            logger.warning(f"[종목리뷰] {symbol} Finnhub 재무 조회 실패: {e.message}")
            return {"조회실패": "재무 데이터를 가져오지 못했다 — 재무 판단은 PER·EPS 등 현재가 지표로만 한다"}

    @staticmethod
    async def judge(collected: CollectedData) -> StockReviewResponse:
        """Gemini 판단 — DB를 쓰지 않는다 (수 분 걸리는 동안 커넥션을 붙잡지 않도록)

        1단계 투자 판단에는 매매 전략 정보를 넣지 않는다. '적합'일 때만 2단계로 전략을 고른다.
        """
        c = collected
        prompt = build_prompt(c.st_code, c.st_nm, c.as_of, c.data)
        review, model = await gemini_api.generate_structured(
            prompt, GeminiReview, system_instruction=build_analysis_instruction(_market(c.mrkt)),
        )

        strategies = TradingStrategyFactory.describe_all()
        swing_type = (
            await StockReviewService._pick_strategy(c, review, strategies) if review.verdict == "적합" else None
        )

        return StockReviewResponse(
            MRKT_CODE=c.mrkt, ST_CODE=c.st_code, ST_NM=c.st_nm,
            VERDICT=review.verdict,
            SUMMARY=review.summary,
            NEWS_VIEW=review.news_market,
            VALUATION_VIEW=review.valuation,
            FINANCIAL_VIEW=review.financials,
            TREND_VIEW=review.trend_flow,
            RECOMMENDED_SWING_TYPE=swing_type,
            RECOMMENDED_STRATEGY_NAME=strategies[swing_type]["이름"] if swing_type else None,
            POSITIVES=review.positives,
            RISKS=review.risks,
            KEY_NEWS=review.key_news,
            DATA_AS_OF=c.last_date,
            INCOMPLETE_BAR=c.bar_incomplete,
            MODEL=model,
            REVIEWED_AT=c.now.isoformat(timespec="seconds"),
        )

    @staticmethod
    async def _pick_strategy(c: CollectedData, review: GeminiReview, strategies: dict[str, dict]) -> Optional[str]:
        """2단계 — '적합' 종목에 맞는 실전 전략 하나 (실전 팩토리에 등록된 전략만 후보)

        전략이 하나뿐이면 고를 것이 없으니 Gemini를 부르지 않는다 (무료 일일 한도 절약).
        선택이 실패해도 투자 판단은 그대로 돌려준다 — 추천 전략만 비운다.
        """
        if len(strategies) == 1:
            return next(iter(strategies))
        try:
            pick, _ = await gemini_api.generate_structured(
                build_strategy_prompt(
                    c.st_code, c.st_nm,
                    analysis={"판단": review.verdict, "요약": review.summary, "주가흐름": review.trend_flow},
                    price=c.data.get("일봉_지표", {}),
                ),
                strategy_pick_schema(list(strategies)),
                system_instruction=build_strategy_instruction(strategies),
            )
            return pick.recommended_swing_type
        except AppError as e:
            logger.warning(f"[종목리뷰] {c.st_code} 전략 선택 실패 — 추천 전략 없이 반환: {e.message}")
            return None

    async def _fetch_bars(self, user_id: str, st_code: str, now: datetime, auth: dict) -> list[dict]:
        """최근 6개월 일봉 (KIS 1회 100봉 제한 → 두 구간으로 나눠 조회)"""
        end = now.date()
        cursor = end - timedelta(days=PRICE_DAYS)
        bars = {}
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=_PRICE_CHUNK_DAYS), end)
            body = await kis_api.get_stock_data(
                user_id, st_code, cursor.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"), self.db,
                access_data=auth,
            )
            for row in body.get("output2") or []:
                if row.get("STCK_BSOP_DATE"):
                    bars[row["STCK_BSOP_DATE"]] = row
            cursor = chunk_end + timedelta(days=1)
        return list(bars.values())

    async def _fetch_news(self, user_id: str, st_code: str, now: datetime, auth: dict) -> list[dict]:
        """최근 7일 뉴스·공시 제목

        TR이 1회 40건(최신순)만 주므로, 받은 목록의 가장 오래된 날짜 전날을 기준일로
        다시 조회하며 7일 전까지 거슬러 올라간다. 기사가 많은 종목은 하루 40건까지만 담긴다.
        """
        cutoff = (now - timedelta(days=NEWS_DAYS)).strftime("%Y%m%d")
        rows = {}
        date = ""
        for _ in range(_NEWS_MAX_CALLS):
            page = await kis_api.get_news_titles(user_id, st_code, self.db, date=date, access_data=auth)
            for row in page:
                rows[row.get("cntt_usiq_srno") or id(row)] = row
            oldest = min((r["data_dt"] for r in page if r.get("data_dt")), default=None)
            if not oldest or oldest <= cutoff:
                break
            date = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        return [r for r in rows.values() if (r.get("data_dt") or "") >= cutoff]


    async def _fetch_us_bars(self, user_id: str, mrkt: str, st_code: str, local_now: datetime,
                             auth: dict) -> list[dict]:
        """최근 6개월 미국 일봉 — 해외 TR은 BYMD 부터 과거로 100봉씩 준다 (뉴욕 날짜 기준)"""
        start = (local_now - timedelta(days=PRICE_DAYS)).strftime("%Y%m%d")
        bymd = local_now.strftime("%Y%m%d")
        bars = {}
        for _ in range(_US_BAR_CALLS):
            body = await foreign_api.get_stock_data(
                user_id, st_code, start, bymd, self.db, excd=mrkt, access_data=auth,
            )
            rows = [r for r in body.get("output2") or [] if r.get("STCK_BSOP_DATE")]
            for row in rows:
                if row["STCK_BSOP_DATE"] >= start:
                    bars[row["STCK_BSOP_DATE"]] = row
            oldest = min((r["STCK_BSOP_DATE"] for r in rows), default=None)
            if not oldest or oldest <= start:
                break
            bymd = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        return list(bars.values())

    async def _fetch_us_news(self, user_id: str, mrkt: str, st_code: str, now: datetime, auth: dict) -> list[dict]:
        """최근 14일 해외 뉴스 제목 — 1회 10건, 마지막 기사의 일시로 이전 페이지를 받는다"""
        cutoff = (now - timedelta(days=US_NEWS_DAYS)).strftime("%Y%m%d")
        rows = {}
        data_dt = data_tm = ""
        for _ in range(_US_NEWS_MAX_CALLS):
            page = await foreign_api.get_news_titles(
                user_id, st_code, self.db, excd=mrkt, data_dt=data_dt, data_tm=data_tm, access_data=auth,
            )
            for row in page:
                rows[row.get("news_key") or id(row)] = row
            if len(page) < 10:
                break
            last = page[-1]
            if (last.get("data_dt") or "") <= cutoff:
                break
            data_dt, data_tm = last.get("data_dt", ""), last.get("data_tm", "")
        return [r for r in rows.values() if (r.get("data_dt") or "") >= cutoff]


async def _run_job(user_id: str, mrkt: str, st_code: str, st_nm: str, token: str) -> None:
    """백그라운드 분석

    요청 세션은 응답과 함께 닫히므로 DB 세션을 따로 열고, 수집이 끝나면 바로 닫는다.
    Gemini 대기(수 분) 동안 커넥션을 붙잡으면 풀(DB_POOL_SIZE)이 마르고 매매 배치가 막힌다.
    """
    keys = _keys(mrkt, st_code)
    started = time.monotonic()
    result: Optional[dict] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    retry_at: Optional[str] = None

    try:
        db = await Database.get_session()
        try:
            collected = await StockReviewService(db).collect(user_id, mrkt, st_code, st_nm)
        finally:
            await db.close()

        review = collected if isinstance(collected, StockReviewResponse) else await StockReviewService.judge(collected)
        result = review.model_dump()
        logger.info(f"[종목리뷰] {st_code} {st_nm} → {review.VERDICT} "
                    f"(model={review.MODEL}, {time.monotonic() - started:.1f}s)")
    except AppError as e:
        error = e.message
        error_code = e.error_code
        retry_at = getattr(e, "retry_at", None)  # QuotaExhaustedError — 한도 초기화 시각
        logger.warning(f"[종목리뷰] {st_code} {st_nm} 실패: {e.message}")
    except Exception as e:
        error = "분석 중 오류가 발생했습니다"
        logger.error(f"[종목리뷰] {st_code} {st_nm} 실패: {e}", exc_info=True)

    redis = await get_redis()
    if result:
        await redis.set(keys["result"], json.dumps(result, ensure_ascii=False), ex=_cache_ttl(_now(), mrkt))

    # 같은 종목을 기다리던 사용자 전원에게 결과를 기록한다 (자기 목록에 사본 —
    # 장중 공유 캐시는 30분이면 사라지므로 늦게 확인해도 결과가 남아 있어야 한다)
    finished_at = _now().isoformat(timespec="seconds")
    for waiter in await redis.smembers(keys["waiters"]):
        raw = await redis.hget(_USER_KEY.format(user_id=waiter), st_code)
        entry = json.loads(raw) if raw else {"MRKT_CODE": mrkt, "ST_CODE": st_code, "ST_NM": st_nm,
                                             "REQUESTED_AT": finished_at}
        if result:
            entry.update(STATUS=STATUS_DONE, RESULT=result, SEEN=False, ERROR=None,
                         ERROR_CODE=None, RETRY_AT=None, FINISHED_AT=finished_at)
        else:
            entry.update(STATUS=STATUS_FAILED, ERROR=error, ERROR_CODE=error_code,
                         RETRY_AT=retry_at, FINISHED_AT=finished_at)
        await _save_entry(redis, waiter, entry)

    await _release(redis, mrkt, st_code, token)
