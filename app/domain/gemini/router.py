"""
Gemini API Router
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated, Optional
from pydantic import BaseModel

from app.common.database import get_db
from app.common.dependencies import get_current_user
from app.core.response import success_response
from app.domain.gemini.service import GeminiService

router = APIRouter(prefix="/gemini", tags=["Gemini"])


class GenerateRequest(BaseModel):
    """텍스트 생성 요청"""
    prompt: str
    model: str = "gemini-2.0-flash"


class ChatMessage(BaseModel):
    """채팅 메시지"""
    role: str  # "user" or "model"
    parts: list[str]


class ChatRequest(BaseModel):
    """채팅 요청"""
    message: str
    history: Optional[list[ChatMessage]] = None
    model: str = "gemini-2.0-flash"


class StockAnalysisRequest(BaseModel):
    """주식 분석 요청"""
    stock_name: str
    stock_data: dict
    model: str = "gemini-2.0-flash"


def get_gemini_service(db: AsyncSession = Depends(get_db)) -> GeminiService:
    """GeminiService 의존성 주입"""
    return GeminiService(db)


@router.post("/generate")
async def generate_content(
    request: GenerateRequest,
    service: Annotated[GeminiService, Depends(get_gemini_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """
    텍스트 생성
    - 사용자의 Google OAuth 토큰으로 Gemini API 호출
    """
    result = await service.generate_content(
        user_id=user_id,
        prompt=request.prompt,
        model=request.model
    )

    return success_response("생성 완료", {"content": result})


@router.post("/chat")
async def chat(
    request: ChatRequest,
    service: Annotated[GeminiService, Depends(get_gemini_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """
    채팅
    - 대화 히스토리 지원
    """
    # history 변환
    history = None
    if request.history:
        history = [
            {"role": msg.role, "parts": msg.parts}
            for msg in request.history
        ]

    result = await service.chat(
        user_id=user_id,
        message=request.message,
        history=history,
        model=request.model
    )

    return success_response("응답 완료", {"content": result})


@router.post("/analyze-stock")
async def analyze_stock(
    request: StockAnalysisRequest,
    service: Annotated[GeminiService, Depends(get_gemini_service)],
    user_id: Annotated[str, Depends(get_current_user)]
):
    """
    주식 분석
    - Gemini를 활용한 주식 데이터 분석
    """
    result = await service.analyze_stock(
        user_id=user_id,
        stock_name=request.stock_name,
        stock_data=request.stock_data,
        model=request.model
    )

    return success_response("분석 완료", {"analysis": result})