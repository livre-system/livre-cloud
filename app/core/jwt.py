import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Header, status
from typing import Optional
from app.core.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_MINUTES


def create_token(id: str, name: str, email: str, device_id: str | None = None, roles: list[str] | None = None) -> str:
    payload = {
        "sub": id,
        "name": name,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    if device_id:
        payload["device_id"] = device_id
    payload["roles"] = roles or ["passenger"]
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


def require_auth(authorization: Optional[str] = Header(default=None)) -> dict:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token requerido")
    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)
    return {
        "id": payload.get("sub"),
        "name": payload.get("name"),
        "email": payload.get("email"),
        "device_id": payload.get("device_id"),
        "roles": payload.get("roles", ["passenger"]),
    }


def optional_auth(authorization: Optional[str] = Header(default=None)) -> dict | None:
    """Read an optional passenger session without making guest flows fail."""
    if not authorization:
        return None
    return require_auth(authorization)


def require_driver(user: dict = Depends(require_auth)) -> dict:
    """Require an authenticated token carrying the driver role."""
    if "driver" not in (user.get("roles") or []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Se requiere el rol de conductor")
    return user
