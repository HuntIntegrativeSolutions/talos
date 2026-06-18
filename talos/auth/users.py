"""Username/password management for local JWT auth (ADR-036)."""
from __future__ import annotations

import psycopg2
import psycopg2.extras
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from talos.db import get_conn

_ph = PasswordHasher()


def add_user(username: str, password: str) -> None:
    """Hash password with argon2id and insert into the users table."""
    hashed = _ph.hash(password)
    # users is not RLS-scoped — no board_scope() needed.
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, hashed_password) VALUES (%s, %s)",
                (username, hashed),
            )
        conn.commit()
    finally:
        conn.close()


def verify_user(username: str, password: str) -> bool:
    """Return True if username exists and password matches the stored argon2id hash."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT hashed_password FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return False
    try:
        _ph.verify(row["hashed_password"], password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
