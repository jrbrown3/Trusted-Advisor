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
import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    Contact, Interaction, User, DiscoveryLayer, ActionStatus,
    RelationshipForecast, TrustTrajectory, Action, ActionUrgency,
)
from app.services import scoring
from app.services import insights_service
from app.services import forecast_service

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


def _json_list(raw: str | None) -> list[str]:
    """Parse a JSON-list text column back into a Python list."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _forecast_inputs(contact: Contact) -> tuple[dict, datetime | None]:
    """
    Build the forecast prompt context AND the 'newest input timestamp' used by
    the cache-freshness check. Assumes contact.interactions / .insights /
    .actions are already eager-loaded.
    """
    interactions = list(contact.interactions)
    insights = list(contact.insights)
    actions = list(contact.actions)

    today = date.today()

    # Days since last interaction.
    days_since_last = "n/a"
    if interactions:
        last = max(i.interaction_date for i in interactions)
        days_since_last = (today - last).days

    open_actions = [a for a in actions if a.status == ActionStatus.PENDING]
    overdue = [a for a in open_actions if a.due_date and a.due_date < today]
    completed = [a for a in actions if a.status == ActionStatus.COMPLETED]

    giving_status = insights_service.giving_signal(
        [ins.delivered_on for ins in insights]
    ).status

    # Recent interactions block, with confirmed debrief themes where present.
    lines = []
    for ix in sorted(interactions, key=lambda i: i.interaction_date, reverse=True)[:6]:
        themes = ""
        d = ix.debrief_draft
        if d is not None and d.status == "confirmed" and d.key_themes:
            parsed = _json_list(d.key_themes)
            if parsed:
                themes = f" — themes: {', '.join(parsed)}"
        lines.append(f"  - {ix.interaction_date} ({ix.medium}), layer {ix.layer_reached}{themes}")
    interactions_block = "\n".join(lines) if lines else "  (none)"

    ctx = {
        "trust_score": contact.trust_score,
        "interaction_count": len(interactions),
        "days_since_last": days_since_last,
        "giving_status": giving_status,
        "open_actions": len(open_actions),
        "overdue_actions": len(overdue),
        "completed_actions": len(completed),
        "interactions_block": interactions_block,
    }

    # Newest relationship-change timestamp for the cache check.
    candidates: list[datetime] = []
    if contact.updated_at:
        candidates.append(contact.updated_at)
    for coll in (interactions, insights, actions):
        for obj in coll:
            if getattr(obj, "created_at", None):
                candidates.append(obj.created_at)
    newest_input_at = max(candidates) if candidates else None

    return ctx, newest_input_at


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
            selectinload(Contact.forecasts),
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

    # Relationship forecast (Feature J): show the latest non-dismissed one,
    # and flag whether it's still fresh under the timestamp-based cache rule.
    _ctx, newest_input_at = _forecast_inputs(contact)
    latest_forecast = max(
        (f for f in contact.forecasts if f.status != "dismissed"),
        key=lambda f: f.created_at or datetime.min,
        default=None,
    )
    forecast_view = None
    if latest_forecast is not None:
        forecast_view = {
            "status": latest_forecast.status,
            "trajectory": latest_forecast.trajectory,
            "risk_flags": _json_list(latest_forecast.risk_flags),
            "opportunities": _json_list(latest_forecast.opportunities),
            "confidence_score": latest_forecast.confidence_score,
            "rationale": latest_forecast.rationale,
            "as_of": latest_forecast.forecast_as_of,
            "llm_provider": latest_forecast.llm_provider,
            "fresh": forecast_service.is_fresh(latest_forecast, newest_input_at),
        }

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
            "forecast": forecast_view,
            "forecast_error": request.query_params.get("forecast_error"),
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


# ─────────────────────────────────────────────────────────────
# Relationship Health Forecast (Feature J) — with caching
# ─────────────────────────────────────────────────────────────

async def _load_contact_full(contact_id: int, user: User, db: AsyncSession) -> Contact:
    """Load a contact with everything the forecast needs."""
    result = await db.execute(
        select(Contact)
        .where(Contact.id == contact_id, Contact.owner_id == user.id)
        .options(
            selectinload(Contact.interactions).selectinload(Interaction.debrief_draft),
            selectinload(Contact.insights),
            selectinload(Contact.actions),
            selectinload(Contact.forecasts),
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    return contact


@router.post("/{contact_id}/forecast")
async def generate_forecast(
    contact_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Generate a forecast, respecting the cache. If a fresh non-dismissed
    forecast already exists (and ?force=1 was not passed), reuse it WITHOUT
    calling the AI. Otherwise generate a new draft.
    """
    contact = await _load_contact_full(contact_id, user, db)
    ctx, newest_input_at = _forecast_inputs(contact)

    force = request.query_params.get("force") == "1"
    latest = max(
        (f for f in contact.forecasts if f.status != "dismissed"),
        key=lambda f: f.created_at or datetime.min,
        default=None,
    )

    # Cache hit: reuse, no AI call.
    if not force and forecast_service.is_fresh(latest, newest_input_at):
        return RedirectResponse(url=f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER)

    # Cache miss: generate.
    try:
        result = await forecast_service.generate_forecast(contact, ctx)
    except forecast_service.ForecastGenerationError as e:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/contacts/{contact_id}?forecast_error={quote(str(e))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Replace any existing unconfirmed DRAFT; keep confirmed ones as history.
    for f in contact.forecasts:
        if f.status == "draft":
            await db.delete(f)
    await db.flush()

    db.add(RelationshipForecast(
        contact_id=contact.id,
        trajectory=result.trajectory,
        risk_flags=json.dumps(result.risk_flags),
        opportunities=json.dumps(result.opportunities),
        confidence_score=result.confidence_score,
        rationale=result.rationale,
        status="draft",
        llm_provider=forecast_service.active_provider(),
        forecast_as_of=date.today(),
    ))
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{contact_id}/forecast/confirm")
async def confirm_forecast(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contact = await _load_contact_full(contact_id, user, db)
    draft = next((f for f in contact.forecasts if f.status == "draft"), None)
    if draft is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No draft forecast to confirm.")

    draft.status = "confirmed"
    draft.confirmed_at = datetime.utcnow()

    # Framework tie-in: an at-risk forecast produces a concrete next move,
    # threading the forecast into the Next Best Action worklist.
    if draft.trajectory == TrustTrajectory.AT_RISK.value:
        db.add(Action(
            contact_id=contact.id,
            action_text=f"Re-engage {contact.full_name} — relationship flagged at risk. "
                        f"Lead with a value-add, not an ask.",
            rationale="Auto-created from a confirmed at-risk relationship forecast.",
            urgency=ActionUrgency.THIS_WEEK.value,
            status=ActionStatus.PENDING,
            ai_generated=True,
            llm_provider=draft.llm_provider,
        ))
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{contact_id}/forecast/dismiss")
async def dismiss_forecast(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contact = await _load_contact_full(contact_id, user, db)
    draft = next((f for f in contact.forecasts if f.status == "draft"), None)
    if draft is not None:
        draft.status = "dismissed"
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=status.HTTP_303_SEE_OTHER)
