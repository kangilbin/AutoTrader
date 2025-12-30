import httpx
from app.exceptions import ExternalServiceError


async def fetch(method: str, url: str, service_name: str = "External API", **kwargs):
    # 연결 시도는 5초, 전체 타임아웃은 10초
    timeout = httpx.Timeout(10.0, connect=5.0)
    method = method.upper()
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
        raise ExternalServiceError(
            service=service_name,
            message="외부 API가 오류 응답을 반환했습니다",
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

