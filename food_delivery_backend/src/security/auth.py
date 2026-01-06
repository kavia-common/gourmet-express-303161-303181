from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import jwt
from passlib.hash import pbkdf2_sha256


# PUBLIC_INTERFACE
def hash_password(password: str) -> str:
    """Hash a plaintext password.

    NOTE: We intentionally use pbkdf2_sha256 instead of bcrypt because the runtime
    environment may ship with bcrypt>=4/5, which is incompatible with passlib's
    bcrypt handler and can cause 500s during registration/login.
    """
    return pbkdf2_sha256.hash(password)


# PUBLIC_INTERFACE
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against the stored hash."""
    return pbkdf2_sha256.verify(plain_password, hashed_password)


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET env var is required")
    return secret


def _jwt_exp_minutes() -> int:
    raw = os.getenv("JWT_EXPIRES_MINUTES", "60")
    try:
        return int(raw)
    except ValueError:
        return 60


# PUBLIC_INTERFACE
def create_access_token(subject: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """Create a signed HS256 JWT access token."""
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_jwt_exp_minutes())).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


# PUBLIC_INTERFACE
def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode/verify a JWT access token, raising JWTError if invalid/expired."""
    return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
