from fastapi import HTTPException, Request
from fastapi_jwt_auth import AuthJWT
from fastapi.middleware.base import BaseHTTPMiddleware
from model.JwtModel import Settings



# JWT 인증을 위한 미들웨어
class JWTAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            # Authorization 헤더에서 토큰 추출
            auth_header = request.headers.get("Authorization")
            if auth_header is None:
                raise HTTPException(status_code=401, detail="Authorization header missing")

            # 토큰에서 'Bearer '을 제외하고 실제 JWT만 추출
            token = auth_header.split(" ")[1]

            # JWT 토큰 인증
            Authorize = AuthJWT()
            Authorize.jwt_required()  # 토큰 검증

        except Exception as e:
            raise HTTPException(status_code=401, detail=str(e))

        response = await call_next(request)
        return response
