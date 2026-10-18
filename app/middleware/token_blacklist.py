"""Middleware that rejects revoked JWTs."""
from datetime import UTC, datetime

from fastapi import Request
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.token_blacklist import TokenBlacklist
from app.models.user import User


class TokenBlacklistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1]
            try:
                payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
                jti = payload.get("jti")
                db = SessionLocal()
                try:
                    # Check jti blacklist
                    if jti and db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first():
                        return JSONResponse(status_code=401, content={"code": 401, "detail": "Token 已被撤销"})

                    # Check user blacklist and token invalidation
                    user_id = payload.get("sub")
                    if user_id:
                        user = db.query(User).filter(User.id == int(user_id)).first()
                        if user and user.is_blacklisted:
                            return JSONResponse(status_code=401, content={"code": 401, "detail": "账号已被禁用"})
                        if user and user.tokens_invalidated_at:
                            iat = payload.get("iat")
                            if iat:
                                token_issued_at = datetime.fromtimestamp(iat, tz=UTC).replace(tzinfo=None)
                                if user.tokens_invalidated_at > token_issued_at:
                                    return JSONResponse(status_code=401, content={"code": 401, "detail": "Token 已被撤销（强制登出）"})
                finally:
                    db.close()
            except JWTError:
                pass
        return await call_next(request)
