"""
app/routers/interactions.py
───────────────────────────
Phase 3 — Interaction Debrief Engine + Feature A (AI debrief).

Routes:
  GET  /interactions                      → list (newest first, with contact)
  GET  /interactions/new                  → log form (?contact_id= preselects)
  POST /interactions                      → create interaction
  GET  /interactions/{id}                 → detail + debrief review
  POST /interactions/{id}/debrief         → generate AI debrief (status='draft')
  POST /interactions/{id}/debrief/confirm → confirm draft → apply side effects
  POST /interactions/{id}/debrief/dismiss → dismiss draft
  POST /interactions/{id}/delete          → delete interaction

Core principle: generating a debrief only ever creates an unconfirmed DRAFT.
Side effects (creating an Action, advancing the contact's discovery layer)
happen ONLY when the user explicitly confirms.
"""

import json
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
    Contact, Interaction, DebriefDraft, Action, User,
    DiscoveryLayer, ActionStatus, ActionUrgency,
)
from app.services import debrief_service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

MEDIUM_OPTIONS = ["Coffee / in-person", "Video call", "Phone call",
                  "Email", "Event / conference", "Other"]


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _json_list(raw: str | None) -> list[str]:
    """Parse a JSON-list text column back into a Python list."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def _get_owned_interaction(
    interaction_id: int, user: User, db: AsyncSession
) -> Interaction:
    """Fetch an interaction, 404 if missing or its contact isn't owned by user."""
    result = await db.execute(
        select(Interaction)
        .join(Contact, Interaction.contact_id == Contact.id)
        .where(Interaction.id == interaction_id, Contact.owner_id == user.id)
        .options(selectinload(Interaction.contact),
                 selectinload(Interaction.debrief_draft))
    )
    interaction = result.scalar_one_or_none()
    if interaction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interaction not found")
    return interaction


# ─────────────────────────────────────────────────────────────
# List
# ─────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_interactions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Interaction)
        .join(Contact, Interaction.contact_id == Contact.id)
        .where(Contact.owner_id == user.id)
        .options(selectinload(Interaction.contact),
                 selectinload(Interaction.debrief_draft))
        .order_by(Interaction.interaction_date.desc(), Interaction.id.desc())
    )
    interactions = result.scalars().all()
    return templates.TemplateResponse(
        request, "pages/interactions_list.html",
        {"interactions": interactions},
    )


# ─────────────────────────────────────────────────────────────
# New / Create
# ─────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_interaction(
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
        # No contacts to log against — send the user to create one first.
        return RedirectResponse(url="/contacts/new", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request, "pages/interaction_form.html",
        {
            "contacts": contacts,
            "preselected_contact_id": contact_id,
            "mediums": MEDIUM_OPTIONS,
            "today": date.today().isoformat(),
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_interaction(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    form = dict(await request.form())

    # Validate the contact belongs to this user.
    try:
        contact_id = int(form.get("contact_id"))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A contact is required.")

    owned = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.owner_id == user.id)
    )
    if owned.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")

    try:
        interaction_date = date.fromisoformat(form.get("interaction_date") or "")
    except ValueError:
        interaction_date = date.today()

    duration = form.get("duration_minutes")
    try:
        duration = int(duration) if duration else None
    except ValueError:
        duration = None

    interaction = Interaction(
        contact_id=contact_id,
        interaction_date=interaction_date,
        medium=(form.get("medium") or "Other").strip(),
        raw_notes=(form.get("raw_notes") or "").strip() or None,
        duration_minutes=duration,
        layer_reached=int(form.get("layer_reached") or DiscoveryLayer.SURFACE),
    )
    db.add(interaction)
    await db.flush()
    new_id = interaction.id
    return RedirectResponse(url=f"/interactions/{new_id}", status_code=status.HTTP_303_SEE_OTHER)


# ─────────────────────────────────────────────────────────────
# Detail + debrief review
# ─────────────────────────────────────────────────────────────

@router.get("/{interaction_id}", response_class=HTMLResponse)
async def interaction_detail(
    interaction_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interaction = await _get_owned_interaction(interaction_id, user, db)
    draft = interaction.debrief_draft

    draft_view = None
    if draft is not None:
        draft_view = {
            "id": draft.id,
            "status": draft.status,
            "key_themes": _json_list(draft.key_themes),
            "trust_signals": _json_list(draft.trust_signals),
            "layer_reached": draft.layer_reached,
            "self_orientation_risk": draft.self_orientation_risk,
            "suggested_next_action": draft.suggested_next_action,
            "follow_up_date": draft.follow_up_date,
            "llm_provider": draft.llm_provider,
        }

    return templates.TemplateResponse(
        request, "pages/interaction_detail.html",
        {
            "interaction": interaction,
            "contact": interaction.contact,
            "draft": draft_view,
            "error": request.query_params.get("error"),
        },
    )


# ─────────────────────────────────────────────────────────────
# Generate debrief (AI) — creates an unconfirmed DRAFT only
# ─────────────────────────────────────────────────────────────

@router.post("/{interaction_id}/debrief")
async def generate_debrief(
    interaction_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interaction = await _get_owned_interaction(interaction_id, user, db)

    try:
        summary = await debrief_service.generate_debrief(interaction.contact, interaction)
    except debrief_service.DebriefGenerationError as e:
        # Surface a friendly error on the detail page rather than 500-ing.
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/interactions/{interaction_id}?error={quote(str(e))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Persist as a DRAFT. Replaces any prior draft for this interaction.
    existing = interaction.debrief_draft
    if existing is not None:
        await db.delete(existing)
        await db.flush()

    draft = DebriefDraft(
        interaction_id=interaction.id,
        key_themes=json.dumps(summary.key_themes),
        trust_signals=json.dumps(summary.trust_signals),
        layer_reached=summary.layer_reached,
        self_orientation_risk=summary.self_orientation_risk,
        suggested_next_action=summary.suggested_next_action,
        follow_up_date=summary.follow_up_date,
        status="draft",
        llm_provider=debrief_service.active_provider(),
    )
    db.add(draft)
    return RedirectResponse(url=f"/interactions/{interaction_id}", status_code=status.HTTP_303_SEE_OTHER)


# ─────────────────────────────────────────────────────────────
# Confirm debrief — THIS is where side effects happen
# ─────────────────────────────────────────────────────────────

@router.post("/{interaction_id}/debrief/confirm")
async def confirm_debrief(
    interaction_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interaction = await _get_owned_interaction(interaction_id, user, db)
    draft = interaction.debrief_draft
    if draft is None or draft.status != "draft":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No draft to confirm.")

    # 1. Mark the draft confirmed.
    draft.status = "confirmed"
    draft.confirmed_at = datetime.utcnow()

    # 2. Advance the contact's deepest discovery layer if the debrief went deeper.
    contact = interaction.contact
    if draft.layer_reached and draft.layer_reached > contact.deepest_layer_reached:
        contact.deepest_layer_reached = draft.layer_reached
    # Also reflect on the interaction itself.
    if draft.layer_reached and draft.layer_reached > interaction.layer_reached:
        interaction.layer_reached = draft.layer_reached

    # 3. Turn the suggested next action into a real, tracked Action.
    if draft.suggested_next_action:
        db.add(Action(
            contact_id=contact.id,
            action_text=draft.suggested_next_action,
            rationale=f"From confirmed debrief of {interaction.interaction_date} "
                      f"({interaction.medium}).",
            urgency=ActionUrgency.THIS_WEEK if draft.follow_up_date else ActionUrgency.WHEN_READY,
            status=ActionStatus.PENDING,
            due_date=draft.follow_up_date,
            ai_generated=True,
            llm_provider=draft.llm_provider,
        ))

    return RedirectResponse(url=f"/interactions/{interaction_id}", status_code=status.HTTP_303_SEE_OTHER)


# ─────────────────────────────────────────────────────────────
# Dismiss debrief — no side effects
# ─────────────────────────────────────────────────────────────

@router.post("/{interaction_id}/debrief/dismiss")
async def dismiss_debrief(
    interaction_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interaction = await _get_owned_interaction(interaction_id, user, db)
    draft = interaction.debrief_draft
    if draft is not None:
        draft.status = "dismissed"
    return RedirectResponse(url=f"/interactions/{interaction_id}", status_code=status.HTTP_303_SEE_OTHER)


# ─────────────────────────────────────────────────────────────
# Delete interaction
# ─────────────────────────────────────────────────────────────

@router.post("/{interaction_id}/delete")
async def delete_interaction(
    interaction_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interaction = await _get_owned_interaction(interaction_id, user, db)
    contact_id = interaction.contact_id
    await db.delete(interaction)
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER)
