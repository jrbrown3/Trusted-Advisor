"""
app/routers/actions.py
──────────────────────
Phase 5 — Next Best Action Engine (Feature 8).

Routes:
  GET  /actions               → prioritized worklist (pending), grouped by band,
                                plus a recently-completed section
  GET  /actions/new           → manual add form (?contact_id= preselects)
  POST /actions               → create a manual action
  POST /actions/{id}/complete → mark complete
  POST /actions/{id}/dismiss  → dismiss
  POST /actions/{id}/reopen   → reopen a completed/dismissed action

The worklist sequencing lives in nba_service. This router loads the data
the scorer needs (each action's contact trust band + giving cadence) and
renders the grouped result.
"""

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    Contact, Action, InsightDelivery, User,
    ActionStatus, ActionUrgency,
)
from app.services import scoring, insights_service, nba_service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

URGENCY_OPTIONS = [
    (ActionUrgency.THIS_WEEK.value, "This week"),
    (ActionUrgency.THIS_MONTH.value, "This month"),
    (ActionUrgency.WHEN_READY.value, "When ready"),
]


async def _get_owned_action(action_id: int, user: User, db: AsyncSession) -> Action:
    result = await db.execute(
        select(Action)
        .join(Contact, Action.contact_id == Contact.id)
        .where(Action.id == action_id, Contact.owner_id == user.id)
        .options(selectinload(Action.contact))
    )
    action = result.scalar_one_or_none()
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    return action


async def _giving_status_map(user: User, db: AsyncSession) -> dict[int, str]:
    """Map contact_id → giving cadence status, for the scorer."""
    result = await db.execute(
        select(InsightDelivery.contact_id, InsightDelivery.delivered_on)
        .join(Contact, InsightDelivery.contact_id == Contact.id)
        .where(Contact.owner_id == user.id)
    )
    dates_by_contact: dict[int, list[date]] = {}
    for contact_id, delivered_on in result.all():
        dates_by_contact.setdefault(contact_id, []).append(delivered_on)
    return {
        cid: insights_service.giving_signal(dates).status
        for cid, dates in dates_by_contact.items()
    }


# ─────────────────────────────────────────────────────────────
# Worklist
# ─────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_actions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Pending actions, with contacts.
    result = await db.execute(
        select(Action)
        .join(Contact, Action.contact_id == Contact.id)
        .where(Contact.owner_id == user.id, Action.status == ActionStatus.PENDING)
        .options(selectinload(Action.contact))
    )
    pending = result.scalars().all()

    giving_map = await _giving_status_map(user, db)

    scored = [
        nba_service.score_action(
            a, a.contact,
            trust_band=scoring.score_band(a.contact.trust_score),
            giving_status=giving_map.get(a.contact_id, "none"),
        )
        for a in pending
    ]
    worklist = nba_service.build_worklist(scored)

    # Recently completed / dismissed (last 10) for context.
    done_result = await db.execute(
        select(Action)
        .join(Contact, Action.contact_id == Contact.id)
        .where(Contact.owner_id == user.id, Action.status != ActionStatus.PENDING)
        .options(selectinload(Action.contact))
        .order_by(Action.completed_at.desc().nullslast(), Action.id.desc())
        .limit(10)
    )
    recent_done = done_result.scalars().all()

    total_pending = len(pending)

    return templates.TemplateResponse(
        request, "pages/actions_list.html",
        {
            "worklist": worklist,
            "total_pending": total_pending,
            "recent_done": recent_done,
        },
    )


# ─────────────────────────────────────────────────────────────
# Manual add
# ─────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_action(
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
        request, "pages/action_form.html",
        {
            "contacts": contacts,
            "preselected_contact_id": contact_id,
            "urgencies": URGENCY_OPTIONS,
            "today": date.today().isoformat(),
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_action(
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

    action_text = (form.get("action_text") or "").strip()
    if not action_text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Action text is required.")

    due_date = None
    if form.get("due_date"):
        try:
            due_date = date.fromisoformat(form["due_date"])
        except ValueError:
            due_date = None

    db.add(Action(
        contact_id=contact_id,
        action_text=action_text,
        rationale=(form.get("rationale") or "").strip() or None,
        urgency=form.get("urgency") or ActionUrgency.WHEN_READY.value,
        status=ActionStatus.PENDING,
        due_date=due_date,
        ai_generated=False,
    ))
    return RedirectResponse(url="/actions", status_code=status.HTTP_303_SEE_OTHER)


# ─────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────

@router.post("/{action_id}/complete")
async def complete_action(
    action_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    action = await _get_owned_action(action_id, user, db)
    action.status = ActionStatus.COMPLETED
    action.completed_at = datetime.utcnow()
    return RedirectResponse(url="/actions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{action_id}/dismiss")
async def dismiss_action(
    action_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    action = await _get_owned_action(action_id, user, db)
    action.status = ActionStatus.DISMISSED
    return RedirectResponse(url="/actions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{action_id}/reopen")
async def reopen_action(
    action_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    action = await _get_owned_action(action_id, user, db)
    action.status = ActionStatus.PENDING
    action.completed_at = None
    return RedirectResponse(url="/actions", status_code=status.HTTP_303_SEE_OTHER)
