"""
표준 응답 모델 및 헬퍼 함수
"""
from pydantic import BaseModel
from typing import TypeVar, Generic, Optional, Any
from datetime import datetime

T = TypeVar('T')


class ApiResponse(BaseModel, Generic[T]):
    """표준 API 응답 모델"""
    success: bool = True
    message: str
    data: Optional[T] = None
    timestamp: datetime = datetime.now()

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat() if dt else None
        }


class ErrorResponse(BaseModel):
    """에러 응답 모델"""
    success: bool = False
    message: str
    error_code: Optional[str] = None
    detail: Optional[Any] = None
    timestamp: datetime = datetime.now()

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat() if dt else None
        }


class PaginatedResponse(BaseModel, Generic[T]):
    """페이지네이션 응답 모델"""
    success: bool = True
    message: str
    data: list[T]
    total: int
    page: int
    size: int
    total_pages: int
    timestamp: datetime = datetime.now()


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
