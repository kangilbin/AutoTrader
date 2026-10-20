"""
Stock Review DTO
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, create_model

Verdict = Literal["적합", "보류", "부적합"]


class StockReviewRequest(BaseModel):
    """종목 리뷰 요청 (국내 종목)"""
    ST_CODE: str


class GeminiReviewBase(BaseModel):
    """Gemini 응답 형식 — description 이 모델에 그대로 전달된다"""
    verdict: Verdict = Field(description="지금 이 가격에 투자하기 합리적인가: 적합 / 보류 / 부적합 (스윙 전략과 무관)")
    summary: str = Field(description="판단 요약 2~3문장, 쉬운 말로")
    news_market: str = Field(description="뉴스·시황: 최근 이 종목과 시장에 무슨 일이 있었나 (날짜 감안, 2~3문장)")
    valuation: str = Field(description="주가 수준: PER·PBR·EPS·ROE·성장률로 볼 때 지금 가격이 비싼가 싼가 (2~3문장)")
    financials: str = Field(description="재무: 실적이 늘고 있나, 빚과 현금 사정은 괜찮나 (2~3문장)")
    trend_flow: str = Field(description="주가 흐름·수급: 최근 추세와 외국인·기관 매매 (2~3문장)")
    positives: list[str] = Field(description="긍정 요인. 근거 수치를 포함")
    risks: list[str] = Field(description="위험 요인. 근거 수치·뉴스를 포함")
    key_news: list[str] = Field(description="판단에 영향을 준 뉴스·공시 제목 (제공된 제목 그대로)")


def review_schema(swing_types: list[str]) -> type[GeminiReviewBase]:
    """등록된 실전 전략 키로 추천 전략 필드를 제한한 응답 스키마

    선택지를 enum 으로 묶어 Gemini가 등록되지 않은 전략을 지어낼 수 없게 하고,
    null 을 허용하지 않아 반드시 하나를 고르게 한다 (화면은 전략 이름만 보여준다).
    """
    swing_type = Literal[tuple(swing_types)]  # type: ignore[valid-type]
    return create_model(
        "GeminiReview",
        __base__=GeminiReviewBase,
        recommended_swing_type=(swing_type, Field(
            description="등록된 스윙 전략 중 이 종목 성격에 가장 맞는 전략의 SWING_TYPE (반드시 하나)",
        )),
    )


class StockReviewResponse(BaseModel):
    """종목 리뷰 응답"""
    MRKT_CODE: str
    ST_CODE: str
    ST_NM: Optional[str] = None
    VERDICT: Verdict
    SUMMARY: str
    # 판단 근거 — 네 관점 (관리종목 등 차단 결과면 비어 있다)
    NEWS_VIEW: Optional[str] = None
    VALUATION_VIEW: Optional[str] = None
    FINANCIAL_VIEW: Optional[str] = None
    TREND_VIEW: Optional[str] = None
    # AI가 등록된 실전 전략 중 고른 것 (SWING_TRADE.SWING_TYPE) — 판단과 별개. 차단 결과면 None
    RECOMMENDED_SWING_TYPE: Optional[str] = None
    RECOMMENDED_STRATEGY_NAME: Optional[str] = None
    POSITIVES: list[str] = []
    RISKS: list[str] = []
    KEY_NEWS: list[str] = []
    BLOCKED_REASONS: list[str] = []      # 관리종목 등 — 있으면 Gemini를 호출하지 않은 결과
    DATA_AS_OF: Optional[str] = None     # 마지막 봉 일자 (YYYYMMDD)
    INCOMPLETE_BAR: bool = False         # 마지막 봉이 장중 미완성
    MODEL: Optional[str] = None          # 판단한 Gemini 모델 (차단 시 None)
    REVIEWED_AT: str                     # 리뷰 생성 시각 (KST ISO) — 공유 캐시에서 받은 결과면 원래 분석 시각
