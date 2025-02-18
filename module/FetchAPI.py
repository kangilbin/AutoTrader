import httpx
from fastapi import HTTPException


async def fetch(method: str, url: str, **kwargs):
    # 연결 시도는 5초, 전체 타임아웃은 10초
    timeout = httpx.Timeout(10.0, connect=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "POST":
                response = await client.post(url, **kwargs)
            elif method.upper() == "GET":
                response = await client.get(url, **kwargs)
            elif method.upper() == "PUT":
                response = await client.put(url, **kwargs)
            elif method.upper() == "DELETE":
                response = await client.delete(url, **kwargs)
            else:
                raise ValueError("지원하지 않는 HTTP 메서드입니다.")

            response.raise_for_status()  # HTTP 오류 상태 코드 처리
            return response.json()

    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Request failed: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"HTTP error occurred: {e}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")
