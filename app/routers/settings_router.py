"""
app/routers/settings_router.py
───────────────────────────────
GET  /settings      → settings page (AI config + account)
POST /settings/ai   → update .env AI keys/provider, reload settings module
POST /settings/password → change password

Design note: the app reads config from a .env file at startup via Pydantic
Settings. We write changes back to .env so they persist across restarts, then
patch the live `settings` object so the change takes effect immediately
without a restart. API key values are masked in the UI (show only last 4 chars)
to avoid leaking them into page source.
"""

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.services.security import verify_password, hash_password

router = APIRouter()
templates = Jinja2Templates(
    directory=Path(__file__).parent.parent / "templates"
)

ENV_FILE = Path(__file__).parent.parent.parent / ".env"


# ── .env helpers ─────────────────────────────────────────────

def _read_env() -> dict[str, str]:
    """Read the .env file into a key→value dict, preserving comments."""
    if not ENV_FILE.exists():
        return {}
    result: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _, v = stripped.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env(updates: dict[str, str]) -> None:
    """Write key=value updates into .env, preserving comments and order.
    Appends any keys that don't already exist."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    lines = ENV_FILE.read_text().splitlines()
    replaced: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.partition("=")[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}")
                replaced.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in replaced:
            new_lines.append(f"{k}={v}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n")


def _mask(value: str) -> str:
    """Show only last 4 chars, rest as asterisks. Empty → empty."""
    if not value or value.startswith("sk-ant-...") or value.startswith("sk-..."):
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def _reload_settings(updates: dict[str, str]) -> None:
    """Patch the live settings singleton so changes take effect immediately."""
    mapping = {
        "LLM_PROVIDER": "llm_provider",
        "LLM_MODEL_CLAUDE": "llm_model_claude",
        "LLM_MODEL_OPENAI": "llm_model_openai",
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "OPENAI_API_KEY": "openai_api_key",
    }
    for env_key, attr in mapping.items():
        if env_key in updates:
            object.__setattr__(settings, attr, updates[env_key])

    # Reset the cached AI clients so next call picks up the new key.
    import app.services.ai_service as ai_svc
    ai_svc._claude_client = None
    ai_svc._openai_client = None


# ── Routes ───────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    env = _read_env()
    anthropic_raw = env.get("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    openai_raw    = env.get("OPENAI_API_KEY",    settings.openai_api_key)
    provider      = env.get("LLM_PROVIDER",      settings.llm_provider)
    model_claude  = env.get("LLM_MODEL_CLAUDE",  settings.llm_model_claude)
    model_openai  = env.get("LLM_MODEL_OPENAI",  settings.llm_model_openai)

    return templates.TemplateResponse(
        request, "pages/settings.html",
        {
            "user": user,
            "provider": provider,
            "model_claude": model_claude,
            "model_openai": model_openai,
            "anthropic_masked": _mask(anthropic_raw),
            "openai_masked":    _mask(openai_raw),
            "anthropic_set":    bool(anthropic_raw and not anthropic_raw.startswith("sk-ant-...")),
            "openai_set":       bool(openai_raw    and not openai_raw.startswith("sk-...")),
            "ai_success": request.query_params.get("ai_success"),
            "pw_success": request.query_params.get("pw_success"),
            "ai_error":   request.query_params.get("ai_error"),
            "pw_error":   request.query_params.get("pw_error"),
        },
    )


@router.post("/ai")
async def save_ai_settings(
    request: Request,
    user: User = Depends(get_current_user),
):
    from urllib.parse import quote
    form = dict(await request.form())

    updates: dict[str, str] = {}

    provider = form.get("llm_provider", "").strip()
    if provider in ("claude", "openai"):
        updates["LLM_PROVIDER"] = provider

    model_claude = form.get("llm_model_claude", "").strip()
    if model_claude:
        updates["LLM_MODEL_CLAUDE"] = model_claude

    model_openai = form.get("llm_model_openai", "").strip()
    if model_openai:
        updates["LLM_MODEL_OPENAI"] = model_openai

    # Keys: only update if the user typed something new (not the masked hint).
    anthropic_key = form.get("anthropic_api_key", "").strip()
    if anthropic_key and "•" not in anthropic_key:
        updates["ANTHROPIC_API_KEY"] = anthropic_key

    openai_key = form.get("openai_api_key", "").strip()
    if openai_key and "•" not in openai_key:
        updates["OPENAI_API_KEY"] = openai_key

    try:
        _write_env(updates)
        _reload_settings(updates)
    except Exception as e:
        return RedirectResponse(
            url=f"/settings?ai_error={quote(str(e)[:120])}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url="/settings?ai_success=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/password")
async def change_password(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from urllib.parse import quote
    form = dict(await request.form())
    current  = form.get("current_password", "")
    new_pw   = form.get("new_password", "")
    confirm  = form.get("confirm_password", "")

    if not verify_password(current, user.hashed_password):
        return RedirectResponse(
            url="/settings?pw_error=" + quote("Current password is incorrect."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if len(new_pw) < 8:
        return RedirectResponse(
            url="/settings?pw_error=" + quote("New password must be at least 8 characters."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if new_pw != confirm:
        return RedirectResponse(
            url="/settings?pw_error=" + quote("Passwords do not match."),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    user.hashed_password = hash_password(new_pw)
    await db.flush()

    return RedirectResponse(
        url="/settings?pw_success=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
