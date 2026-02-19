"""
표준 응답 헬퍼 함수
"""
from typing import Optional, Any
from datetime import datetime


def success_response(message: str, data: Any = None) -> dict:
    """성공 응답 생성 헬퍼"""
    return {
        "success": True,
        "message": message,
        "data": data,
        "timestamp": datetime.now().isoformat()
    }


def error_response(
    message: str,
    error_code: Optional[str] = None,
    detail: Any = None
) -> dict:
    """에러 응답 생성 헬퍼"""
    return {
        "success": False,
        "message": message,
        "error_code": error_code,
        "detail": detail,
        "timestamp": datetime.now().isoformat()
    }


def paginated_response(
    message: str,
    data: list,
    total: int,
    page: int,
    size: int
) -> dict:
    """페이지네이션 응답 생성 헬퍼"""
    total_pages = (total + size - 1) // size if size > 0 else 0
    return {
        "success": True,
        "message": message,
        "data": data,
        "total": total,
        "page": page,
        "size": size,
        "total_pages": total_pages,
        "timestamp": datetime.now().isoformat()
    }
