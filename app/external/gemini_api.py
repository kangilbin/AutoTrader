"""
Google Gemini API 클라이언트 — 서버 키 하나로 호출한다.

사용자 OAuth 토큰으로 호출해도 쿼터·요금은 OAuth 클라이언트의 프로젝트(=이 서버)에
쌓이므로, 사용자별 토큰을 쓸 이유가 없다 (토큰 만료·scope 검증 부담만 생긴다).
"""
import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Type, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.core.config import get_settings
from app.exceptions import ConfigurationError, ExternalServiceError, QuotaExhaustedError

logger = logging.getLogger(__name__)
settings = get_settings()

T = TypeVar("T", bound=BaseModel)

# 모델마다 한 번만 시도하고, 아래 실패면 바로 다음 모델로 넘어간다.
# 무료 등급은 '실패한 요청'도 일일 한도(모델별 하루 20회)에 센다 — AI Studio 에서 3.8 이 23/20 으로
# 표시됐다. 같은 모델 재시도는 혼잡한 시간에 한도를 두 배로 태우므로 하지 않는다.
# 429: 분당(5회)·일일 한도 / 5xx: 혼잡·일시 오류 / 504: 호출 상한(_CALL_TIMEOUT_MS) 초과 —
# SDK 가 클라이언트 타임아웃을 서버 마감으로도 보내므로 느린 모델은 504로 돌아온다 / 403·404: 쓸 수 없는 모델
_NEXT_MODEL_CODES = {403, 404, 429, 500, 502, 503, 504}
# 호출 1회 상한 — 무료 등급 정상 응답이 60~130초, 혼잡 시 더 길다
_CALL_TIMEOUT_MS = 240_000
# 무료 등급 일일 한도(모델별, 예: 3.8-flash 하루 20회)를 다 쓴 모델은 초기화 시각까지 건너뛴다.
# 재시도해도 같은 429 만 오고, 그 사이 다른 모델로 넘어갈 시간만 버린다.
_DAILY_QUOTA_FALLBACK_SEC = 3600
# 모델 → 한도 초기화 시각(epoch 초). 사용자에게 '몇 시 이후'로 안내하므로 벽시계 기준으로 둔다
_exhausted_until: dict[str, float] = {}
_KST = ZoneInfo("Asia/Seoul")

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


def quota_reset_at() -> Optional[datetime]:
    """대체 모델까지 전부 일일 한도를 다 썼으면 가장 이른 초기화 시각(KST), 아니면 None

    분석을 시작하기 전에 확인한다 — 다 쓴 상태로 시작하면 KIS 조회만 하고 실패한다.
    (서버 프로세스 메모리 기준이라 재시작 직후엔 모델마다 한 번씩 429 를 받아야 다시 안다)
    """
    now = time.time()
    resets = [_exhausted_until.get(m, 0) for m in _model_chain()]
    if resets and all(r > now for r in resets):
        return datetime.fromtimestamp(min(resets), _KST)
    return None


def quota_exhausted_error(reset: datetime) -> QuotaExhaustedError:
    today = datetime.now(_KST).date()
    when = f"{reset:%H:%M}" if reset.date() == today else f"내일 {reset:%H:%M}"
    return QuotaExhaustedError(
        "Gemini", f"오늘 AI 무료 사용량을 모두 썼습니다. {when} 이후 다시 시도해주세요",
        retry_at=reset.isoformat(timespec="seconds"),
    )


def _daily_quota_delay(e: errors.APIError) -> Optional[int]:
    """일일 한도 초과 429 면 초기화까지 남은 초, 아니면 None (분당 한도 429 는 None)"""
    if e.code != 429:
        return None
    details = ((e.details or {}).get("error") or {}).get("details") or []
    per_day = any(
        "PerDay" in (v.get("quotaId") or "")
        for d in details if "QuotaFailure" in (d.get("@type") or "")
        for v in d.get("violations") or []
    )
    if not per_day:
        return None
    for d in details:
        delay = d.get("retryDelay") if "RetryInfo" in (d.get("@type") or "") else None
        if delay and delay.endswith("s"):
            try:
                return int(float(delay[:-1]))
            except ValueError:
                break
    return _DAILY_QUOTA_FALLBACK_SEC


def _config(schema, system_instruction: Optional[str], low_thinking: bool) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=schema,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        # 이 작업엔 깊은 추론이 필요 없다 — 기본값이면 3.x 가 추론 토큰을 수천~1만 개 써서 응답이 늦어진다
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW) if low_thinking else None,
    )


async def generate_structured(
    prompt: str,
    schema: Type[T],
    *,
    system_instruction: Optional[str] = None,
) -> tuple[T, str]:
    """프롬프트를 보내고 schema 형식의 JSON 응답을 검증된 객체로 돌려준다

    무료 등급은 모델별로 혼잡·한도가 따로 오고, 실패한 요청도 일일 한도에 센다.
    - 모델마다 한 번만 시도한다. 혼잡·한도·시간 초과면 바로 다음 모델로
    - 일일 한도 초과면 그 모델은 초기화 시각까지 호출하지 않는다
    - 그 밖의 요청 오류(400 등)는 모델을 바꿔도 같으므로 바로 올린다
      (단, 모델이 추론 수준 설정을 거부하면 설정 없이 한 번 더 보낸다)

    Returns:
        (검증된 응답, 실제로 응답한 모델명)
    """
    last_error: Optional[Exception] = None
    chain = _model_chain()
    for model in chain:
        if _exhausted_until.get(model, 0) > time.time():
            logger.info(f"[Gemini] {model} 오늘 무료 한도 소진 — 건너뜀")
            continue

        for low_thinking in (True, False):
            try:
                response = await _get_client().aio.models.generate_content(
                    model=model, contents=prompt, config=_config(schema, system_instruction, low_thinking),
                )
                return _parse(response, model), model
            except errors.APIError as e:
                last_error = e
                if e.code == 400 and low_thinking and "thinking" in (e.message or "").lower():
                    continue  # 이 모델은 추론 수준 지정을 지원하지 않음 — 기본값으로 다시
                logger.warning(f"[Gemini] 호출 실패 code={e.code} model={model} → 다음 모델: {e.message}")
                daily_delay = _daily_quota_delay(e)
                if daily_delay is not None:
                    _exhausted_until[model] = time.time() + daily_delay
                if e.code not in _NEXT_MODEL_CODES:
                    raise ExternalServiceError("Gemini", e.message or str(e), original_error=e)
                break
            except (httpx.TimeoutException, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(f"[Gemini] 응답 시간 초과 model={model} → 다음 모델")
                break

    reset = quota_reset_at()
    if reset:
        raise quota_exhausted_error(reset)
    message = "AI 서버가 혼잡해 응답하지 않습니다. 잠시 후 다시 시도해주세요"
    if any(_exhausted_until.get(m, 0) > time.time() for m in chain):
        message += " (일부 모델은 오늘 사용량 소진)"
    raise ExternalServiceError("Gemini", message, status_code=503, original_error=last_error)


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
