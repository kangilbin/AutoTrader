"""
Gemini Service - Google Gemini API 비즈니스 로직
"""
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging

from google import genai
from google.oauth2.credentials import Credentials

from app.domain.oauth.service import OAuthService
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class GeminiService:
    """Gemini 서비스"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.oauth_service = OAuthService(db)

    async def _get_client(self, user_id: str) -> genai.Client:
        """
        사용자별 Gemini 클라이언트 생성
        - 사용자의 Google OAuth 토큰 사용
        """
        # 유효한 Google 토큰 가져오기 (만료 시 에러)
        access_token = await self.oauth_service.get_valid_google_token(user_id)

        # Credentials 생성
        credentials = Credentials(token=access_token)

        # Gemini 클라이언트 생성
        client = genai.Client(credentials=credentials)

        return client

    async def generate_content(
        self,
        user_id: str,
        prompt: str,
        model: str = "gemini-2.0-flash"
    ) -> str:
        """
        텍스트 생성
        """
        client = await self._get_client(user_id)

        response = client.models.generate_content(
            model=model,
            contents=prompt
        )

        return response.text

    async def chat(
        self,
        user_id: str,
        message: str,
        history: Optional[list] = None,
        model: str = "gemini-2.0-flash"
    ) -> str:
        """
        채팅 (대화 히스토리 지원)
        """
        client = await self._get_client(user_id)

        # 히스토리가 있으면 채팅 세션 생성
        if history:
            chat = client.chats.create(
                model=model,
                history=history
            )
            response = chat.send_message(message)
        else:
            response = client.models.generate_content(
                model=model,
                contents=message
            )

        return response.text

    async def analyze_stock(
        self,
        user_id: str,
        stock_name: str,
        stock_data: dict,
        model: str = "gemini-2.0-flash"
    ) -> str:
        """
        주식 분석 (AutoTrader 특화 기능)
        """
        prompt = f"""
당신은 전문 주식 분석가입니다. 다음 주식 데이터를 분석해주세요.

종목명: {stock_name}
데이터: {stock_data}

다음 항목을 포함해서 분석해주세요:
1. 현재 추세 분석
2. 기술적 지표 해석
3. 매수/매도 시점 제안
4. 리스크 요인
"""
        return await self.generate_content(user_id, prompt, model)