"""
app/routers/setup.py
────────────────────
First-run setup. Mounted at root (no prefix).

  GET  /setup → provisioning form + environment/config status. Redirects to
                /auth/login once a user already exists (setup is one-time).
  POST /setup → create the first user, sign them in, land on the dashboard.

This is the experience a brand-new install lands on: get_current_user raises
RequiresSetup when zero users exist, which redirects here.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import count_users, set_session_cookie
from app.models import User
from app.services.security import hash_password, create_access_token

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD = 8


def _config_status() -> dict:
    """Read-only view of the AI configuration, surfaced during setup so the
    operator can confirm the environment is wired up."""
    provider = settings.llm_provider
    key_set = bool(
        settings.anthropic_api_key if provider == "claude" else settings.openai_api_key
    )
    return {
        "provider": provider,
        "model": settings.active_model,
        "key_set": key_set,
    }


@router.get("/setup", response_class=HTMLResponse)
async def setup_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if await count_users(db) > 0:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "pages/setup.html",
        {"error": None, "form": {}, "config": _config_status()},
    )


@router.post("/setup")
async def setup_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Guard: setup is one-time. If a user was created in the meantime, bail.
    if await count_users(db) > 0:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    form = dict(await request.form())
    username = (form.get("username") or "").strip()
    email = (form.get("email") or "").strip()
    password = form.get("password") or ""
    confirm = form.get("confirm_password") or ""

    def fail(msg: str):
        return templates.TemplateResponse(
            request, "pages/setup.html",
            {
                "error": msg,
                "form": {"username": username, "email": email},
                "config": _config_status(),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Validation.
    if not username or len(username) < 3:
        return fail("Choose a username of at least 3 characters.")
    if not _EMAIL_RE.match(email):
        return fail("Enter a valid email address.")
    if len(password) < _MIN_PASSWORD:
        return fail(f"Password must be at least {_MIN_PASSWORD} characters.")
    if password != confirm:
        return fail("The passwords don't match.")

    # Create the first user.
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        is_active=True,
    )
    db.add(user)
    await db.flush()

    # Sign them in immediately.
    token = create_access_token(user.username)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, token)
    return response
