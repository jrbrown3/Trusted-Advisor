"""
app/services/forecast_service.py
────────────────────────────────
Feature J — Relationship Health Forecast (with caching).

Forecasts are expensive (a full AI call analysing the whole relationship),
so they must not regenerate on every page load. The caching strategy here is
TIMESTAMP-BASED STALENESS rather than a stored fingerprint:

  A forecast is fresh if
    (a) it was generated AFTER the most recent relationship event
        (a trust-score edit, or a new interaction / insight / action), AND
    (b) it is younger than MAX_AGE_DAYS.

This needs no extra schema column — it reuses created_at / updated_at that
already exist on the records. (When Alembic lands and we move to Postgres, a
dedicated fingerprint column could replace this, but for now this is the
correct minimal design.)

Generation itself follows the same draft-then-confirm, Pydantic-gated pattern
as the debrief and agenda services. This module never writes to the DB.
"""

import json
import logging
from datetime import datetime, timedelta

from pydantic import ValidationError

from app.config import settings
from app.models import Contact, RelationshipForecast
from app.schemas.forecast import RelationshipForecastResult
from app.services import ai_service

logger = logging.getLogger(__name__)

# A forecast older than this is always considered stale, even if nothing
# about the relationship has changed — relationships drift, and a months-old
# read shouldn't be trusted.
MAX_AGE_DAYS = 14


class ForecastGenerationError(Exception):
    """Raised when a forecast can't be generated or validated."""
    pass


def is_fresh(
    forecast: RelationshipForecast | None,
    newest_input_at: datetime | None,
    *,
    now: datetime | None = None,
    max_age_days: int = MAX_AGE_DAYS,
) -> bool:
    """
    True if a cached forecast can be reused.

    forecast        — the latest non-dismissed forecast, or None
    newest_input_at — max(contact.updated_at, latest interaction/insight/action
                      created_at); the moment the relationship last changed
    """
    if forecast is None or forecast.status == "dismissed":
        return False
    if forecast.created_at is None:
        return False

    now = now or datetime.utcnow()
    # (b) age window
    if now - forecast.created_at > timedelta(days=max_age_days):
        return False
    # (a) any relationship change after this forecast invalidates it
    if newest_input_at is not None and newest_input_at > forecast.created_at:
        return False
    return True


_SYSTEM_PROMPT = """\
You are a Trusted Advisor relationship analyst. You assess the trajectory of a
single advisor–executive relationship from its evidence and project where it is
heading. You are candid, not optimistic — a forecast that flatters is worthless.

Judge trajectory on real signals, weighted through the Trust Equation
(Credibility + Reliability + Intimacy) / Self-Orientation:

  - Reliability: are commitments being kept? Overdue or abandoned actions
    erode the trust already earned and pull a relationship toward DECLINING.
  - Intimacy / depth: is the relationship progressing through the discovery
    layers (1 surface → 4 personal), or stalled at the surface?
  - Giving cadence: an active cadence of giving value lowers self-orientation
    and strengthens trust; a cold cadence is a leading indicator of decline.
  - Interaction frequency and recency: long gaps weaken even strong ties.

Trajectory options: "strengthening", "stable", "declining", "at_risk".
Use "at_risk" only when multiple signals point to real jeopardy."""


def _build_user_prompt(contact: Contact, ctx: dict) -> str:
    interactions_block = ctx.get("interactions_block", "  (none)")
    return f"""\
CONTACT
  Name:  {contact.full_name}
  Title: {contact.title or "—"}
  Trust dimensions (0–10): credibility={contact.credibility}, \
reliability={contact.reliability}, intimacy={contact.intimacy}, \
self_orientation={contact.self_orientation}
  Computed trust score: {ctx.get('trust_score')}
  Deepest discovery layer reached: {contact.deepest_layer_reached} of 4

SIGNALS
  Total interactions: {ctx.get('interaction_count', 0)}
  Days since last interaction: {ctx.get('days_since_last', 'n/a')}
  Giving cadence: {ctx.get('giving_status', 'none')}
  Open actions: {ctx.get('open_actions', 0)} (overdue: {ctx.get('overdue_actions', 0)})
  Completed actions: {ctx.get('completed_actions', 0)}

RECENT INTERACTIONS (most recent first)
{interactions_block}

TASK
Produce a forecast as JSON with exactly these keys:
  "trajectory":       one of "strengthening" | "stable" | "declining" | "at_risk"
  "risk_flags":       array of specific risks (may be empty)
  "opportunities":    array of concrete openings to deepen the relationship
  "confidence_score": a number between 0 and 1
  "rationale":        one short paragraph of evidence-based reasoning
Return only the JSON object."""


async def generate_forecast(contact: Contact, ctx: dict) -> RelationshipForecastResult:
    """Generate and validate a forecast. Returns the validated result (NOT
    persisted). Raises ForecastGenerationError on failure."""
    try:
        raw = await ai_service.complete_json(_SYSTEM_PROMPT, _build_user_prompt(contact, ctx), max_tokens=900)
    except ai_service.AIServiceError as e:
        logger.warning(f"Forecast AI call failed: {e}")
        raise ForecastGenerationError(
            "The AI provider couldn't generate a forecast. "
            "Check your API key and provider setting, then try again."
        ) from e

    try:
        return RelationshipForecastResult.model_validate(raw)
    except ValidationError as e:
        logger.warning(f"Forecast validation failed: {e}")
        raise ForecastGenerationError(
            "The AI returned a response that didn't match the expected forecast "
            "format. Try regenerating — this is usually transient."
        ) from e


def active_provider() -> str:
    return settings.llm_provider
