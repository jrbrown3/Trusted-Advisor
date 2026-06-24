"""
app/dependencies.py
───────────────────
Authentication dependencies and session helpers.

Session model: a signed JWT (PyJWT, HS256) carried in an httpOnly cookie.
On every protected request, get_current_user reads the cookie, decodes it,
and loads the user. If there's no valid session it raises a redirect:
  - to /setup  when NO users exist yet (first-run provisioning), or
  - to /auth/login  when users exist but this request isn't authenticated.

The two exceptions are translated into 303 redirects by handlers in main.py.
Because get_current_user keeps the same name and return type as the old dev
stub, no router call sites change — they just become genuinely protected.
"""

from fastapi import Depends, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.config import settings
from app.database import get_db
from app.models import User
from app.services.security import decode_access_token

SESSION_COOKIE = "ta_session"


# ── Redirect signals (handled in main.py) ────────────────────

class RequiresSetup(Exception):
    """No users exist yet — send the visitor to first-run setup."""


class RequiresLogin(Exception):
    """Users exist but this request isn't authenticated."""


# ── Cookie helpers ───────────────────────────────────────────

def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=not settings.app_debug,           # https-only in production
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# ── User lookups ─────────────────────────────────────────────

async def count_users(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(User))
    return int(result.scalar_one())


async def _user_from_request(request: Request, db: AsyncSession) -> User | None:
    """Resolve the current user from the session cookie, or None."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    username = decode_access_token(token)
    if not username:
        return None
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


# ── Dependencies ─────────────────────────────────────────────

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Protected-route dependency. Returns the authenticated user or raises
    a redirect (to setup or login)."""
    user = await _user_from_request(request, db)
    if user is not None:
        return user
    # Not authenticated — decide where to send them.
    if await count_users(db) == 0:
        raise RequiresSetup()
    raise RequiresLogin()


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Non-raising variant for public pages (login/setup) that just need to
    know whether someone is already signed in."""
    return await _user_from_request(request, db)
