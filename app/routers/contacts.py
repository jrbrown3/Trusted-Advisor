"""
app/routers/contacts.py
───────────────────────
Phase 2 — Trust Scorecard.

Routes:
  GET  /contacts                 → list, sorted by trust score (descending)
  GET  /contacts/new             → blank evidence-anchored form
  POST /contacts                 → create from form
  GET  /contacts/{id}            → detail page (the scorecard)
  GET  /contacts/{id}/edit       → pre-filled form
  POST /contacts/{id}/edit       → update
  POST /contacts/{id}/delete     → delete
  POST /contacts/score-preview   → HTMX: live score fragment as answers change

Every score is derived from the evidence-anchored answers in scoring.py.
The user picks diagnostic answers; the app derives C/R/I/SO from them.
"""

from pathlib import Path
from datetime import date

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Contact, Interaction, User, DiscoveryLayer, ActionStatus
from app.services import scoring
from app.services import insights_service

router = APIRouter()

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _parse_dimension_scores(form: dict) -> dict[str, float]:
    """
    Pull the four dimension scores out of submitted form data.
    Each dimension arrives as a radio value keyed by the dimension key
    (e.g. form["credibility"] == "7.5"). Falls back to a neutral 5.0 if a
    dimension is missing or unparseable.
    """
    scores: dict[str, float] = {}
    for dim in scoring.DIMENSIONS:
        raw = form.get(dim.key)
        try:
            scores[dim.key] = float(raw) if raw is not None else 5.0
        except (TypeError, ValueError):
            scores[dim.key] = 5.0
    return scores


async def _get_owned_contact(
    contact_id: int, user: User, db: AsyncSession
) -> Contact:
    """Fetch a contact, 404 if missing or not owned by the current user."""
    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.owner_id == user.id,
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    return contact


# ─────────────────────────────────────────────────────────────
# List
# ─────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def list_contacts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Contact).where(Contact.owner_id == user.id)
    )
    contacts = result.scalars().all()

    # Sort by computed trust score (descending). Done in Python because
    # trust_score is a computed property, not a stored column.
    contacts_sorted = sorted(contacts, key=lambda c: c.trust_score, reverse=True)

    rows = [
        {
            "contact": c,
            "score": c.trust_score,
            "band": scoring.score_band(c.trust_score),
        }
        for c in contacts_sorted
    ]

    return templates.TemplateResponse(
        request,
        "pages/contacts_list.html",
        {"rows": rows},
    )


# ─────────────────────────────────────────────────────────────
# New / Create
# ─────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
async def new_contact(
    request: Request,
    user: User = Depends(get_current_user),
):
    preselected = {dim.key: 5.0 for dim in scoring.DIMENSIONS}
    preview = scoring.compute_trust_score(**preselected)

    return templates.TemplateResponse(
        request,
        "pages/contact_form.html",
        {
            "mode": "new",
            "contact": None,
            "dimensions": scoring.DIMENSIONS,
            "selected": preselected,
            "preview_score": preview,
            "preview_band": scoring.score_band(preview),
            "layers": list(DiscoveryLayer),
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_contact(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    form = dict(await request.form())
    scores = _parse_dimension_scores(form)

    contact = Contact(
        owner_id=user.id,
        full_name=(form.get("full_name") or "").strip() or "Unnamed contact",
        title=(form.get("title") or "").strip() or None,
        organization=(form.get("organization") or "").strip() or None,
        linkedin_url=(form.get("linkedin_url") or "").strip() or None,
        notes=(form.get("notes") or "").strip() or None,
        credibility=scores["credibility"],
        reliability=scores["reliability"],
        intimacy=scores["intimacy"],
        self_orientation=scores["self_orientation"],
        deepest_layer_reached=int(form.get("deepest_layer_reached") or DiscoveryLayer.SURFACE),
    )
    db.add(contact)
    await db.flush()
    new_id = contact.id

    return RedirectResponse(
        url=f"/contacts/{new_id}", status_code=status.HTTP_303_SEE_OTHER
    )


# ─────────────────────────────────────────────────────────────
# Live score preview (HTMX)
# ─────────────────────────────────────────────────────────────

@router.post("/score-preview", response_class=HTMLResponse)
async def score_preview(request: Request):
    """
    Returns just the score-badge fragment. Triggered by HTMX on every answer
    change in the form, so the user sees the Trust Equation update live before
    saving anything. No DB access — pure computation.
    """
    form = dict(await request.form())
    scores = _parse_dimension_scores(form)
    preview = scoring.compute_trust_score(**scores)

    return templates.TemplateResponse(
        request,
        "components/score_badge.html",
        {
            "preview_score": preview,
            "preview_band": scoring.score_band(preview),
            "scores": scores,
        },
    )


# ─────────────────────────────────────────────────────────────
# Detail (the scorecard)
# ─────────────────────────────────────────────────────────────

@router.get("/{contact_id}", response_class=HTMLResponse)
async def contact_detail(
    contact_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Eager-load interactions and their debrief drafts: the detail template
    # iterates them, and async SQLAlchemy forbids lazy-loading on access.
    result = await db.execute(
        select(Contact)
        .where(Contact.id == contact_id, Contact.owner_id == user.id)
        .options(
            selectinload(Contact.interactions).selectinload(Interaction.debrief_draft),
            selectinload(Contact.insights),
            selectinload(Contact.actions),
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    score = contact.trust_score

    # Giving cadence — the antidote to self-orientation, surfaced on the card.
    giving = insights_service.giving_signal(
        [ins.delivered_on for ins in contact.insights]
    )

    breakdown = []
    for dim in scoring.DIMENSIONS:
        value = getattr(contact, dim.key)
        breakdown.append({
            "name": dim.name,
            "blurb": dim.blurb,
            "value": value,
            "lower_is_better": dim.lower_is_better,
        })

    return templates.TemplateResponse(
        request,
        "pages/contact_detail.html",
        {
            "contact": contact,
            "score": score,
            "band": scoring.score_band(score),
            "breakdown": breakdown,
            "deepest_layer": contact.deepest_layer_reached,
            "giving": giving,
            "insights": sorted(
                contact.insights, key=lambda i: i.delivered_on, reverse=True
            ),
            "open_actions": sorted(
                [a for a in contact.actions if a.status == ActionStatus.PENDING],
                key=lambda a: (a.due_date is None, a.due_date or date.max),
            ),
        },
    )


# ─────────────────────────────────────────────────────────────
# Edit / Update
# ─────────────────────────────────────────────────────────────

@router.get("/{contact_id}/edit", response_class=HTMLResponse)
async def edit_contact(
    contact_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contact = await _get_owned_contact(contact_id, user, db)

    selected = {
        dim.key: scoring.closest_option_value(dim.key, getattr(contact, dim.key))
        for dim in scoring.DIMENSIONS
    }
    preview = scoring.compute_trust_score(**selected)

    return templates.TemplateResponse(
        request,
        "pages/contact_form.html",
        {
            "mode": "edit",
            "contact": contact,
            "dimensions": scoring.DIMENSIONS,
            "selected": selected,
            "preview_score": preview,
            "preview_band": scoring.score_band(preview),
            "layers": list(DiscoveryLayer),
        },
    )


@router.post("/{contact_id}/edit", response_class=HTMLResponse)
async def update_contact(
    contact_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contact = await _get_owned_contact(contact_id, user, db)
    form = dict(await request.form())
    scores = _parse_dimension_scores(form)

    contact.full_name = (form.get("full_name") or "").strip() or contact.full_name
    contact.title = (form.get("title") or "").strip() or None
    contact.organization = (form.get("organization") or "").strip() or None
    contact.linkedin_url = (form.get("linkedin_url") or "").strip() or None
    contact.notes = (form.get("notes") or "").strip() or None
    contact.credibility = scores["credibility"]
    contact.reliability = scores["reliability"]
    contact.intimacy = scores["intimacy"]
    contact.self_orientation = scores["self_orientation"]
    contact.deepest_layer_reached = int(
        form.get("deepest_layer_reached") or contact.deepest_layer_reached
    )

    return RedirectResponse(
        url=f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER
    )


# ─────────────────────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────────────────────

@router.post("/{contact_id}/delete")
async def delete_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contact = await _get_owned_contact(contact_id, user, db)
    await db.delete(contact)
    return RedirectResponse(url="/contacts", status_code=status.HTTP_303_SEE_OTHER)
