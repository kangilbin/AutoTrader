# app/external/headers.py
"""
KIS 공통 헤더 유틸

국내/해외 API로 확장할 것을 고려해서, '공통으로 유지되는 헤더 조립'만 담당합니다.
- authorization (Bearer)
- appkey
- appsecret
- tr_id (엔드포인트별로 달라서 호출자가 지정)
- custtype (대부분 "P", 필요 없으면 None)

주의:
- 여기서는 네트워크 호출/Redis 접근을 하지 않습니다.
- access_data는 kis_api.py에서 가져온 dict를 그대로 받는 형태를 전제로 합니다.
"""
from typing import Any, Dict, Mapping, Optional


def kis_headers(
    access_data: Mapping[str, Any],
    *,
    tr_id: str,
    cust_type: Optional[str] = "P",
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    KIS 요청 헤더 생성.

    Args:
        access_data: {"access_token": ..., "api_key": ..., "secret_key": ...} 를 포함하는 매핑
        tr_id: KIS TR-ID (필수)
        cust_type: 기본 "P". 필요 없으면 None으로 전달
        extra: 추가/덮어쓸 헤더

    Returns:
        dict: headers
    """
    access_token = access_data.get("access_token")
    api_key = access_data.get("api_key")
    secret_key = access_data.get("secret_key")

    headers: Dict[str, str] = {
        "authorization": f"Bearer {access_token}" if access_token else "",
        "appkey": str(api_key) if api_key is not None else "",
        "appsecret": str(secret_key) if secret_key is not None else "",
        "tr_id": tr_id,
    }

    if cust_type is not None:
        headers["custtype"] = cust_type

    if extra:
        headers.update(extra)

    return headers


def kis_error_message(response: Mapping[str, Any], default: str) -> str:
    """
    KIS 응답에서 사람이 읽을 만한 에러 메시지를 우선순위로 선택.
    """
    return (
        response.get("error_description")
        or response.get("error_code")
        or response.get("msg1")
        or default
    )