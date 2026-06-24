"""
app/services/security.py
────────────────────────
Password hashing and JWT token utilities.

Uses `bcrypt` directly for password hashing (passlib 1.7.x is unmaintained
and breaks against bcrypt 5.x) and `PyJWT` for tokens.

PyJWT (rather than python-jose) because python-jose 3.3.0 carried two CVEs
— algorithm confusion with ECDSA/OpenSSH keys (CVE-2024-33663) and a JWE
"JWT bomb" DoS (CVE-2024-33664) — and is comparatively under-maintained.
We only ever self-issue and self-verify single-algorithm HS256 tokens, so
our exposure was low, but PyJWT is the better-maintained, smaller-surface
choice for exactly this use case.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from jwt import PyJWTError

from app.config import settings

ALGORITHM = "HS256"
_BCRYPT_MAX_BYTES = 72   # bcrypt hard limit


# ── Password hashing ──────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a plaintext password. Truncates to bcrypt's 72-byte limit."""
    pw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a stored hash."""
    pw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except ValueError:
        return False


# ── JWT tokens ────────────────────────────────────────────────

def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    """Create a signed JWT for the given subject (typically username or user id)."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """Return the subject from a valid token, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except PyJWTError:
        return None
