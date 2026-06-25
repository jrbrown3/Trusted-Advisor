"""
app/routers/dashboard.py
────────────────────────
Phase 8 — Dashboard (the landing view).

Single route: GET / renders the unified portfolio dashboard. All aggregation
lives in dashboard_service; this router just loads the data it needs.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Contact, Interaction, User
from app.services import dashboard_service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Load the whole portfolio with everything the aggregation needs.
    result = await db.execute(
        select(Contact)
        .where(Contact.owner_id == user.id)
        .options(
            selectinload(Contact.insights),
            selectinload(Contact.actions),
            selectinload(Contact.forecasts),
            selectinload(Contact.interactions).selectinload(Interaction.debrief_draft),
        )
    )
    contacts = list(result.scalars().all())

    data = dashboard_service.build_dashboard(contacts)

    return templates.TemplateResponse(
        request, "pages/dashboard.html",
        {
            "d": data,
            "onboard": request.query_params.get("onboard") == "1",
        },
    )
