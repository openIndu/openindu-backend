"""FastAPI dependency injection helpers."""
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.identity.infrastructure.tokens import decode_token
from app.models.token_blacklist import TokenBlacklist
from app.models.user import User

security = HTTPBearer(auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User | None:
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        return None
    if payload.get("type") != "access":
        return None
    jti = payload.get("jti")
    if jti and db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first():
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or user.deleted_at is not None or not user.is_active or user.is_blacklisted:
        return None
    # Check if tokens were invalidated after this token was issued
    if user.tokens_invalidated_at:
        iat = payload.get("iat")
        if iat:
            token_issued_at = datetime.fromtimestamp(iat, tz=UTC).replace(tzinfo=None)
            if user.tokens_invalidated_at > token_issued_at:
                return None
    return user


async def require_auth(current_user: User | None = Depends(get_current_user)) -> User:
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return current_user


async def require_member(current_user: User = Depends(require_auth)) -> User:
    if current_user.role not in ("member", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要会员权限")
    return current_user


async def require_admin(current_user: User = Depends(require_auth)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user
