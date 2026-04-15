import asyncio
import logging

import httpx
from app.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

RATE_LIMIT_MSG = "초당 거래건수를 초과"
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_DELAY = 1.0


async def fetch(method: str, url: str, service_name: str = "External API", **kwargs):
    # 연결 시도는 5초, 전체 타임아웃은 10초
    timeout = httpx.Timeout(10.0, connect=5.0)
    method = method.upper()

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status() # 4xx/5xx는 예외로 처리

                try:
                    return {
                        "body": response.json(),
                        "header": dict(response.headers),
                    }
                except ValueError as e:
                    raise ExternalServiceError(
                        service=service_name,
                        message="응답이 JSON 형식이 아닙니다",
                        status_code=502,
                        original_error=e,
                        detail={
                            "url": url,
                            "method": method,
                            "status_code": response.status_code,
                            "response_text": response.text,
                        },
                    )

        except httpx.TimeoutException as e:
            raise ExternalServiceError(
                service=service_name,
                message="요청 시간 초과",
                status_code=504,
                original_error=e
            )
        except httpx.HTTPStatusError as e:
            error_msg = e.response.json().get('msg1', '')

            # 초당 거래건수 초과 시 재시도
            if RATE_LIMIT_MSG in error_msg and attempt < MAX_RATE_LIMIT_RETRIES:
                delay = RATE_LIMIT_DELAY * (attempt + 1)
                logger.warning(f"[{service_name}] 초당 거래건수 초과, {delay}초 후 재시도 ({attempt + 1}/{MAX_RATE_LIMIT_RETRIES})")
                await asyncio.sleep(delay)
                continue

            raise ExternalServiceError(
                service=service_name,
                message=error_msg,
                status_code=502,
                original_error=e,
                detail={
                    "url": str(e.request.url),
                    "method": method,
                    "status_code": e.response.status_code,
                    "response_text": e.response.text,
                },
            )
        except httpx.RequestError as e:
            raise ExternalServiceError(
                service=service_name,
                message=f"요청 실패: {str(e)}",
                status_code=503,
                original_error=e
            )