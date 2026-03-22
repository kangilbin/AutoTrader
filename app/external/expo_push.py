"""
Expo Push Notification API 클라이언트
https://docs.expo.dev/push-notifications/sending-notifications/
"""
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
MAX_BATCH_SIZE = 100
MAX_RETRIES = 3


async def send_expo_push(
    push_tokens: list[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> list[str]:
    """
    Expo Push API로 알림 전송

    Args:
        push_tokens: Expo Push Token 목록
        title: 알림 제목
        body: 알림 본문
        data: 앱에 전달할 추가 데이터

    Returns:
        실패한 토큰 목록 (DeviceNotRegistered)
    """
    failed_tokens = []

    for i in range(0, len(push_tokens), MAX_BATCH_SIZE):
        chunk = push_tokens[i:i + MAX_BATCH_SIZE]
        messages = [
            {
                "to": token,
                "title": title,
                "body": body,
                "sound": "default",
                "data": data or {},
            }
            for token in chunk
        ]

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                    response = await client.post(EXPO_PUSH_URL, json=messages)
                    response.raise_for_status()

                result = response.json()
                tickets = result.get("data", [])

                for idx, ticket in enumerate(tickets):
                    if ticket.get("status") == "error":
                        error_type = ticket.get("details", {}).get("error", "")
                        if error_type == "DeviceNotRegistered":
                            failed_tokens.append(chunk[idx])
                            logger.info(f"Expo 토큰 만료: {chunk[idx][:30]}...")
                        else:
                            logger.warning(f"Expo Push 에러: {ticket.get('message')}")
                break

            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Expo Push 재시도 {attempt + 1}/{MAX_RETRIES}: {e}")
                else:
                    logger.error(f"Expo Push 최종 실패: {e}")
            except Exception as e:
                logger.error(f"Expo Push 예상치 못한 에러: {e}", exc_info=True)
                break

    return failed_tokens
