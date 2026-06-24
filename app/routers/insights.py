"""
app/routers/insights.py
───────────────────────
Phase 4 — Insight Delivery Log (Feature 7).

Routes:
  GET  /insights              → global log, newest first, with giving cadence
  GET  /insights/new          → log form (?contact_id= preselects)
  POST /insights              → create
  GET  /insights/{id}/edit    → edit form (incl. noting the response)
  POST /insights/{id}/edit    → update
  POST /insights/{id}/delete  → delete

This log is the antidote to Self-Orientation: it records the value you GIVE.
The list surfaces each contact's giving cadence so you can see, at a glance,
which high-value relationships have gone cold on giving.
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Contact, InsightDelivery, User
from app.services import insights_service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

MEDIUM_OPTIONS = ["Email", "Call", "In-person", "Message / DM", "Shared doc", "Other"]


async def _get_owned_insight(
    insight_id: int, user: User, db: AsyncSession
) -> InsightDelivery:
    """Fetch an insight, 404 if missing or its contact isn't owned by user."""
    result = await db.execute(
        select(InsightDelivery)
        .join(Contact, InsightDelivery.contact_id == Contact.id)
        .where(InsightDelivery.id == insight_id, Contact.owner_id == user.id)
        .options(selectinload(InsightDelivery.contact))
    )
    insight = result.scalar_one_or_none()
    if insight is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Insight not found")
    return insight


# ─────────────────────────────────────────────────────────────
# List — with per-contact giving cadence
# ─────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_insights(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # All insights for this user's contacts, newest first.
    result = await db.execute(
        select(InsightDelivery)
        .join(Contact, InsightDelivery.contact_id == Contact.id)
        .where(Contact.owner_id == user.id)
        .options(selectinload(InsightDelivery.contact))
        .order_by(InsightDelivery.delivered_on.desc(), InsightDelivery.id.desc())
    )
    insights = result.scalars().all()

    # Compute giving cadence per contact from all their delivery dates.
    dates_by_contact: dict[int, list[date]] = {}
    for ins in insights:
        dates_by_contact.setdefault(ins.contact_id, []).append(ins.delivered_on)
    signal_by_contact = {
        cid: insights_service.giving_signal(dates)
        for cid, dates in dates_by_contact.items()
    }

    rows = [
        {"insight": ins, "signal": signal_by_contact.get(ins.contact_id)}
        for ins in insights
    ]

    return templates.TemplateResponse(
        request, "pages/insights_list.html", {"rows": rows},
    )


# ─────────────────────────────────────────────────────────────
# New / Create
# ─────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_insight(
    request: Request,
    contact_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Contact).where(Contact.owner_id == user.id).order_by(Contact.full_name)
    )
    contacts = result.scalars().all()
    if not contacts:
        return RedirectResponse(url="/contacts/new", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request, "pages/insight_form.html",
        {
            "mode": "new",
            "insight": None,
            "contacts": contacts,
            "preselected_contact_id": contact_id,
            "mediums": MEDIUM_OPTIONS,
            "today": date.today().isoformat(),
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_insight(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    form = dict(await request.form())

    try:
        contact_id = int(form.get("contact_id"))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A contact is required.")

    owned = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.owner_id == user.id)
    )
    if owned.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")

    insight_text = (form.get("insight_text") or "").strip()
    if not insight_text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Insight text is required.")

    try:
        delivered_on = date.fromisoformat(form.get("delivered_on") or "")
    except ValueError:
        delivered_on = date.today()

    insight = InsightDelivery(
        contact_id=contact_id,
        insight_text=insight_text,
        source=(form.get("source") or "").strip() or None,
        delivered_on=delivered_on,
        medium=(form.get("medium") or "").strip() or None,
        response_noted=(form.get("response_noted") or "").strip() or None,
    )
    db.add(insight)
    await db.flush()
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER)


# ─────────────────────────────────────────────────────────────
# Edit / Update  (also where you note how the insight landed)
# ─────────────────────────────────────────────────────────────

@router.get("/{insight_id}/edit", response_class=HTMLResponse)
async def edit_insight(
    insight_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    insight = await _get_owned_insight(insight_id, user, db)
    result = await db.execute(
        select(Contact).where(Contact.owner_id == user.id).order_by(Contact.full_name)
    )
    contacts = result.scalars().all()

    return templates.TemplateResponse(
        request, "pages/insight_form.html",
        {
            "mode": "edit",
            "insight": insight,
            "contacts": contacts,
            "preselected_contact_id": insight.contact_id,
            "mediums": MEDIUM_OPTIONS,
            "today": date.today().isoformat(),
        },
    )


@router.post("/{insight_id}/edit", response_class=HTMLResponse)
async def update_insight(
    insight_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    insight = await _get_owned_insight(insight_id, user, db)
    form = dict(await request.form())

    text = (form.get("insight_text") or "").strip()
    if text:
        insight.insight_text = text
    insight.source = (form.get("source") or "").strip() or None
    insight.medium = (form.get("medium") or "").strip() or None
    insight.response_noted = (form.get("response_noted") or "").strip() or None
    try:
        insight.delivered_on = date.fromisoformat(form.get("delivered_on") or "")
    except ValueError:
        pass  # keep existing date on bad input

    return RedirectResponse(url=f"/contacts/{insight.contact_id}", status_code=status.HTTP_303_SEE_OTHER)


# ─────────────────────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────────────────────

@router.post("/{insight_id}/delete")
async def delete_insight(
    insight_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    insight = await _get_owned_insight(insight_id, user, db)
    contact_id = insight.contact_id
    await db.delete(insight)
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER)
