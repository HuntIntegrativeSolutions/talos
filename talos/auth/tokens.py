"""JWT issue and validation for local human gate authentication (ADR-036)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt

from talos.auth.users import verify_user


def _get_secret() -> str:
    secret = os.environ.get("TALOS_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "TALOS_JWT_SECRET env var is required. "
            "Set it before starting the server."
        )
    return secret


def issue_token(username: str, password: str) -> str:
    """Verify credentials and return a signed human JWT.

    Raises ValueError if the credentials are invalid.
    """
    if not verify_user(username, password):
        raise ValueError("invalid credentials")
    expiry_hours = int(os.environ.get("TALOS_JWT_EXPIRY_HOURS", "8"))
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "token_class": "human",
        "iat": now,
        "exp": now + timedelta(hours=expiry_hours),
    }
    return jwt.encode(payload, _get_secret(), algorithm="HS256")


def validate_token(token: str) -> dict:
    """Decode and return JWT claims.

    Raises jwt.PyJWTError on invalid signature, expiry, or malformed token.
    """
    return jwt.decode(token, _get_secret(), algorithms=["HS256"])
