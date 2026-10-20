"""
Google Gemini API 클라이언트 — 서버 키 하나로 호출한다.

사용자 OAuth 토큰으로 호출해도 쿼터·요금은 OAuth 클라이언트의 프로젝트(=이 서버)에
쌓이므로, 사용자별 토큰을 쓸 이유가 없다 (토큰 만료·scope 검증 부담만 생긴다).
"""
import asyncio
import logging
from typing import Optional, Type, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.core.config import get_settings
from app.exceptions import ConfigurationError, ExternalServiceError

logger = logging.getLogger(__name__)
settings = get_settings()

T = TypeVar("T", bound=BaseModel)

# 무료 등급 분당 한도 초과(429)·과부하(503)·일시 서버 오류(500/502/504)는 잠시 뒤 재시도하면 되는 상태다
_RETRY_LATER_CODES = {429, 500, 502, 503, 504}
# 모델이 없어졌거나(404) 이 키로 쓸 수 없는 경우 — 기다리지 않고 다음 모델로
_SKIP_MODEL_CODES = {403, 404}
_ATTEMPTS_PER_MODEL = 2
_RETRY_WAIT_SEC = 10
# 호출 1회 상한 — 무료 등급 정상 응답이 60~90초. 없으면 멈춘 호출이 작업을 무기한 붙잡는다
_CALL_TIMEOUT_MS = 180_000

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GEMINI_API_KEY:
            raise ConfigurationError("GEMINI_API_KEY가 설정되지 않았습니다", config_key="GEMINI_API_KEY")
        _client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=_CALL_TIMEOUT_MS),
        )
    return _client


def _model_chain() -> list[str]:
    fallbacks = [m.strip() for m in settings.GEMINI_FALLBACK_MODELS.split(",") if m.strip()]
    return list(dict.fromkeys([settings.GEMINI_MODEL, *fallbacks]))


async def generate_structured(
    prompt: str,
    schema: Type[T],
    *,
    system_instruction: Optional[str] = None,
) -> tuple[T, str]:
    """프롬프트를 보내고 schema 형식의 JSON 응답을 검증된 객체로 돌려준다

    무료 등급은 모델별로 혼잡이 따로 오므로, 429·5xx·타임아웃이면 잠시 뒤 같은 모델을 한 번 더
    시도하고 그래도 막히면 다음 모델로 넘어간다. 요청 자체의 오류(400 등)는 모델을
    바꿔도 같으므로 바로 올린다.

    Returns:
        (검증된 응답, 실제로 응답한 모델명)
    """
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=schema,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    last_error: Optional[Exception] = None
    for model in _model_chain():
        for attempt in range(1, _ATTEMPTS_PER_MODEL + 1):
            try:
                response = await _get_client().aio.models.generate_content(
                    model=model, contents=prompt, config=config,
                )
                return _parse(response, model), model
            except errors.APIError as e:
                last_error = e
                logger.warning(f"[Gemini] 호출 실패 code={e.code} model={model} ({attempt}/{_ATTEMPTS_PER_MODEL}): {e.message}")
                if e.code in _SKIP_MODEL_CODES:
                    break
                if e.code not in _RETRY_LATER_CODES:
                    raise ExternalServiceError("Gemini", e.message or str(e), original_error=e)
                if attempt < _ATTEMPTS_PER_MODEL:
                    await asyncio.sleep(_RETRY_WAIT_SEC)
            except (httpx.TimeoutException, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(f"[Gemini] 응답 시간 초과 model={model} ({attempt}/{_ATTEMPTS_PER_MODEL})")
                if attempt < _ATTEMPTS_PER_MODEL:
                    await asyncio.sleep(_RETRY_WAIT_SEC)

    raise ExternalServiceError(
        "Gemini", "모든 모델이 응답하지 않습니다. 잠시 후 다시 시도해주세요",
        status_code=503, original_error=last_error,
    )


def _parse(response: types.GenerateContentResponse, model: str):
    usage = response.usage_metadata
    if usage:
        logger.info(
            f"[Gemini] model={model} prompt={usage.prompt_token_count} "
            f"output={usage.candidates_token_count} thoughts={usage.thoughts_token_count}"
        )

    if response.parsed is None:
        # 안전 필터 차단·토큰 한도 초과 등으로 본문이 비거나 스키마를 벗어난 경우
        reason = response.candidates[0].finish_reason if response.candidates else None
        raise ExternalServiceError("Gemini", f"응답을 해석할 수 없습니다 (finish_reason={reason})")
    return response.parsed
