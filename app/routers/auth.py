"""
app/routers/auth.py
───────────────────
Login and logout. Mounted at /auth.

  GET  /auth/login   → login form (redirects to /setup if no users exist,
                       or to / if already signed in)
  POST /auth/login   → verify credentials, set session cookie, go to /
  POST /auth/logout  → clear session cookie, go to /auth/login
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    get_optional_user, count_users,
    set_session_cookie, clear_session_cookie,
)
from app.models import User
from app.services.security import verify_password, create_access_token

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current=Depends(get_optional_user),
):
    if current is not None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    if await count_users(db) == 0:
        return RedirectResponse(url="/setup", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "pages/login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    form = dict(await request.form())
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        # Re-render with a generic error (don't reveal which field was wrong).
        return templates.TemplateResponse(
            request, "pages/login.html",
            {"error": "Incorrect username or password.", "username": username},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = create_access_token(user.username)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, token)
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response
