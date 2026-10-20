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
    verdict: Verdict = Field(description="스윙 자동매매 등록 판단: 적합 / 보류 / 부적합")
    summary: str = Field(description="판단 요약 2~3문장")
    positives: list[str] = Field(description="긍정 요인. 근거 수치를 포함")
    risks: list[str] = Field(description="위험 요인. 근거 수치·뉴스를 포함")
    key_news: list[str] = Field(description="판단에 영향을 준 뉴스·공시 제목 (제공된 제목 그대로)")


def review_schema(swing_types: list[str]) -> type[GeminiReviewBase]:
    """등록된 실전 전략 키로 추천 전략 필드를 제한한 응답 스키마

    선택지를 enum 으로 묶어 Gemini가 등록되지 않은 전략을 지어낼 수 없게 한다.
    """
    swing_type = Literal[tuple(swing_types)]  # type: ignore[valid-type]
    return create_model(
        "GeminiReview",
        __base__=GeminiReviewBase,
        recommended_swing_type=(Optional[swing_type], Field(
            description="이 종목에 가장 맞는 전략의 SWING_TYPE. 어느 전략과도 맞지 않으면 null",
        )),
        strategy_reason=(str, Field(
            description="그 전략을 고른 이유와, 그 전략 기준으로 지금 어디쯤인지 "
                        "(예: EMA20 아래 하락 추세라 진입 조건까지 대기 필요). 고르지 않았다면 그 이유",
        )),
    )


class StockReviewResponse(BaseModel):
    """종목 리뷰 응답"""
    MRKT_CODE: str
    ST_CODE: str
    ST_NM: Optional[str] = None
    VERDICT: Verdict
    SUMMARY: str
    RECOMMENDED_SWING_TYPE: Optional[str] = None     # AI가 고른 전략 (SWING_TRADE.SWING_TYPE). 없으면 등록 비추천
    RECOMMENDED_STRATEGY_NAME: Optional[str] = None
    STRATEGY_REASON: Optional[str] = None
    POSITIVES: list[str] = []
    RISKS: list[str] = []
    KEY_NEWS: list[str] = []
    BLOCKED_REASONS: list[str] = []      # 관리종목 등 — 있으면 Gemini를 호출하지 않은 결과
    DATA_AS_OF: Optional[str] = None     # 마지막 봉 일자 (YYYYMMDD)
    INCOMPLETE_BAR: bool = False         # 마지막 봉이 장중 미완성
    MODEL: Optional[str] = None          # 판단한 Gemini 모델 (차단 시 None)
    REVIEWED_AT: str                     # 리뷰 생성 시각 (KST ISO) — 공유 캐시에서 받은 결과면 원래 분석 시각
