"""
도메인 예외 - 비즈니스 규칙 위반

Service, Repository, Entity에서 사용
전역 핸들러에서 4xx HTTP 응답으로 변환
"""
from typing import Any, Optional
from app.exceptions.base import AppError


class DomainError(AppError):
    """도메인 예외 베이스"""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "DOMAIN_ERROR",
        status_code: int = 400,
        detail: Optional[Any] = None
    ):
        super().__init__(
            message,
            error_code=error_code,
            status_code=status_code,
            detail=detail
        )


class ValidationError(DomainError):
    """
    유효성 검증 실패

    사용처: Entity 검증, Request DTO 검증
    HTTP: 422 Unprocessable Entity
    """

    def __init__(self, message: str, *, field: Optional[str] = None, detail: Optional[Any] = None):
        super().__init__(
            message,
            error_code="VALIDATION_ERROR",
            status_code=422,
            detail=detail or {"field": field} if field else None
        )
        self.field = field


class NotFoundError(DomainError):
    """
    리소스를 찾을 수 없음

    사용처: Repository에서 조회 실패
    HTTP: 404 Not Found
    """

    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            f"{resource}을(를) 찾을 수 없습니다: {identifier}",
            error_code="NOT_FOUND",
            status_code=404,
            detail={"resource": resource, "identifier": str(identifier)}
        )
        self.resource = resource
        self.identifier = identifier


class DuplicateError(DomainError):
    """
    중복 리소스

    사용처: 유니크 제약 위반 (Service에서 IntegrityError 변환)
    HTTP: 409 Conflict
    """

    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            f"이미 존재하는 {resource}입니다: {identifier}",
            error_code="DUPLICATE",
            status_code=409,
            detail={"resource": resource, "identifier": str(identifier)}
        )
        self.resource = resource
        self.identifier = identifier


class BusinessRuleError(DomainError):
    """
    비즈니스 규칙 위반

    사용처: Entity, Service에서 도메인 규칙 위반
    HTTP: 400 Bad Request

    예시:
    - 매수/매도 비율 합이 100 초과
    - 잔고 부족
    - 거래 시간 외
    """

    def __init__(self, message: str, *, rule: Optional[str] = None, detail: Optional[Any] = None):
        super().__init__(
            message,
            error_code="BUSINESS_RULE_VIOLATION",
            status_code=400,
            detail=detail or {"rule": rule} if rule else None
        )
        self.rule = rule


class PermissionDeniedError(DomainError):
    """
    권한 없음 (도메인 레벨)

    사용처: Service에서 소유권 검증 실패
    HTTP: 403 Forbidden

    예시: 다른 사용자의 계좌 접근 시도
    """

    def __init__(self, message: str = "접근 권한이 없습니다", *, detail: Optional[Any] = None):
        super().__init__(
            message,
            error_code="PERMISSION_DENIED",
            status_code=403,
            detail=detail
        )