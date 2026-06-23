"""
app/main.py
───────────
FastAPI application factory.

Startup sequence:
  1. Create DB tables (idempotent — safe to run on every boot in dev)
  2. Mount static files
  3. Register routers
  4. Register exception handlers
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import engine, Base

# Import models so SQLAlchemy registers them with Base metadata
import app.models  # noqa: F401

# Routers (stubs for now — filled in per phase)
from app.routers import auth, contacts, interactions, insights, actions, dashboard


# ── Lifespan ─────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown: dispose connection pool
    await engine.dispose()


# ── App factory ───────────────────────────────────────────────

app = FastAPI(
    title="Trusted Advisor",
    version="0.1.0",
    debug=settings.app_debug,
    lifespan=lifespan,
    # Hide docs in production
    docs_url="/docs" if settings.app_debug else None,
    redoc_url=None,
)


# ── Static files ──────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Templates ─────────────────────────────────────────────────

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ── Routers ───────────────────────────────────────────────────

app.include_router(auth.router,         prefix="/auth",         tags=["auth"])
app.include_router(contacts.router,     prefix="/contacts",     tags=["contacts"])
app.include_router(interactions.router, prefix="/interactions", tags=["interactions"])
app.include_router(insights.router,     prefix="/insights",     tags=["insights"])
app.include_router(actions.router,      prefix="/actions",      tags=["actions"])
app.include_router(dashboard.router,                            tags=["dashboard"])


# ── Root redirect ─────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root(request: Request):
    return templates.TemplateResponse("pages/dashboard.html", {"request": request})


# ── Exception handlers ────────────────────────────────────────

@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        "pages/404.html", {"request": request}, status_code=404
    )
