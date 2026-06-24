"""
app/services/debrief_service.py
───────────────────────────────
Feature A — AI Debrief Auto-Summarization.

Flow:
  1. Build a framework-aware prompt from the contact + interaction notes.
  2. Call the LLM through the single ai_service integration point.
  3. Validate the response against schemas.DebriefSummary (Pydantic).
  4. Return the validated object. The CALLER decides whether to persist it
     as a draft — this service never writes to the DB.

If the model fails or returns an invalid shape, DebriefGenerationError is
raised with a human-readable message the router can show.
"""

import logging

from pydantic import ValidationError

from app.config import settings
from app.models import Contact, Interaction
from app.schemas.debrief import DebriefSummary
from app.services import ai_service

logger = logging.getLogger(__name__)


class DebriefGenerationError(Exception):
    """Raised when a debrief can't be generated or validated."""
    pass


_SYSTEM_PROMPT = """\
You are a Trusted Advisor debrief analyst. You analyse notes from a \
relationship-building interaction with a senior executive and extract a \
concise, honest structured debrief.

Apply two frameworks:

1. The Trust Equation: Trust = (Credibility + Reliability + Intimacy) / \
Self-Orientation. Self-Orientation is the silent deal-killer — be candid \
when the interaction was steered toward the advisor's own agenda rather \
than the executive's world.

2. The four Discovery Layers:
   1 Surface     — role, org structure, stated priorities
   2 Operational — KPIs, pressures, team dynamics, budget authority
   3 Strategic   — long-term agenda, board pressures, competitive fears
   4 Personal    — career motivations, legacy intent, what keeps them up at night

Be specific and evidence-based. Do not flatter. If the notes show the \
conversation stayed at the surface, say so honestly via layer_reached. \
If the advisor talked more than they listened, reflect that in \
self_orientation_risk."""


def _build_user_prompt(contact: Contact, interaction: Interaction) -> str:
    """Assemble the per-interaction context for the model."""
    notes = (interaction.raw_notes or "").strip() or "(no notes recorded)"
    return f"""\
CONTACT
  Name:         {contact.full_name}
  Title:        {contact.title or "—"}
  Organization: {contact.organization or "—"}
  Current trust dimensions (0–10): \
credibility={contact.credibility}, reliability={contact.reliability}, \
intimacy={contact.intimacy}, self_orientation={contact.self_orientation}
  Deepest layer reached so far: {contact.deepest_layer_reached}

INTERACTION
  Date:     {interaction.interaction_date}
  Medium:   {interaction.medium}
  Duration: {interaction.duration_minutes or "—"} min
  Notes:
{notes}

TASK
Produce a debrief as JSON with exactly these keys:
  "key_themes":            array of 1–5 short strings
  "trust_signals":         array of short strings (moments that built or eroded trust)
  "layer_reached":         integer 1–4 (deepest layer this interaction actually reached)
  "self_orientation_risk": one of "low", "medium", "high"
  "suggested_next_action": one short string, the single most important follow-up
  "follow_up_date":        an ISO date "YYYY-MM-DD" or null
Return only the JSON object."""


async def generate_debrief(contact: Contact, interaction: Interaction) -> DebriefSummary:
    """
    Generate and validate a debrief for one interaction.

    Returns a validated DebriefSummary (NOT persisted).
    Raises DebriefGenerationError on any failure.
    """
    system = _SYSTEM_PROMPT
    user = _build_user_prompt(contact, interaction)

    try:
        raw = await ai_service.complete_json(system, user, max_tokens=800)
    except ai_service.AIServiceError as e:
        logger.warning(f"Debrief AI call failed: {e}")
        raise DebriefGenerationError(
            "The AI provider couldn't generate a debrief. "
            "Check your API key and provider setting, then try again."
        ) from e

    try:
        return DebriefSummary.model_validate(raw)
    except ValidationError as e:
        logger.warning(f"Debrief validation failed: {e}")
        raise DebriefGenerationError(
            "The AI returned a response that didn't match the expected debrief "
            "format. Try regenerating — this is usually transient."
        ) from e


def active_provider() -> str:
    """Which provider generated a debrief — recorded on the draft for provenance."""
    return settings.llm_provider
