"""
app/services/agenda_service.py
──────────────────────────────
Feature G — AI Meeting Agenda Builder.

Flow mirrors the debrief service:
  1. Build a framework-aware prompt from the contact AND the full
     relationship history (past interactions, confirmed debrief themes,
     trust dimensions, giving cadence, open actions).
  2. Call the LLM through the single ai_service integration point.
  3. Validate against schemas.MeetingAgendaPlan (Pydantic).
  4. Return the validated object. The caller persists it as a draft.

The prompt's whole job is to produce an agenda that goes DEEPER (toward
Layers 3-4) while LEADING WITH VALUE — never a sales script.
"""

import json
import logging

from pydantic import ValidationError

from app.config import settings
from app.models import Contact, Interaction
from app.schemas.agenda import MeetingAgendaPlan
from app.services import ai_service

logger = logging.getLogger(__name__)


class AgendaGenerationError(Exception):
    """Raised when an agenda can't be generated or validated."""
    pass


_SYSTEM_PROMPT = """\
You are a Trusted Advisor meeting strategist. You prepare an advisor for an \
upcoming conversation with a senior executive. Your output is an agenda whose \
purpose is to DEEPEN the relationship, not to sell.

Hold two frameworks throughout:

1. The four Discovery Layers — the agenda should move the conversation past \
the surface:
   1 Surface     — role, org structure, stated priorities
   2 Operational — KPIs, pressures, team dynamics, budget authority
   3 Strategic   — long-term agenda, board pressures, competitive fears
   4 Personal    — career motivations, legacy intent, what keeps them up at night
   Aim the discovery_questions at Layers 3 and 4. Surface-level questions are \
a wasted meeting.

2. The Trust Equation — keep Self-Orientation LOW. Every trust_building_move \
should give value first: an insight, an introduction, useful candor. Never \
propose pitching, closing, or steering toward the advisor's own goals. If the \
relationship is early, the agenda should earn the right to go deeper, not rush it.

Be specific to THIS person and THIS history. Generic agendas are useless. \
Reference what's known about them."""


def _build_user_prompt(
    contact: Contact,
    prior_interactions: list[Interaction],
    *,
    giving_status: str,
    open_action_texts: list[str],
    objective: str | None,
    meeting_medium: str,
    meeting_date: str,
) -> str:
    """Assemble the relationship context for the model."""

    # Summarise prior interactions (most recent first), including confirmed
    # debrief themes where present.
    history_lines: list[str] = []
    for ix in sorted(prior_interactions, key=lambda i: i.interaction_date, reverse=True)[:6]:
        themes = ""
        draft = ix.debrief_draft
        if draft is not None and draft.status == "confirmed" and draft.key_themes:
            try:
                parsed = json.loads(draft.key_themes)
                if parsed:
                    themes = f" — themes: {', '.join(parsed)}"
            except (json.JSONDecodeError, TypeError):
                pass
        history_lines.append(
            f"  - {ix.interaction_date} ({ix.medium}), reached layer {ix.layer_reached}{themes}"
        )
    history_block = "\n".join(history_lines) if history_lines else "  (no prior interactions logged)"

    actions_block = (
        "\n".join(f"  - {t}" for t in open_action_texts[:6])
        if open_action_texts else "  (none)"
    )

    objective_block = (
        f"\nADVISOR'S OBJECTIVE FOR THIS MEETING\n  {objective.strip()}\n"
        if objective and objective.strip() else ""
    )

    return f"""\
CONTACT
  Name:         {contact.full_name}
  Title:        {contact.title or "—"}
  Organization: {contact.organization or "—"}
  Trust dimensions (0–10): credibility={contact.credibility}, \
reliability={contact.reliability}, intimacy={contact.intimacy}, \
self_orientation={contact.self_orientation}
  Deepest layer reached so far: {contact.deepest_layer_reached} of 4
  Giving cadence: {giving_status}

RELATIONSHIP HISTORY (most recent first)
{history_block}

OPEN COMMITMENTS / ACTIONS
{actions_block}
{objective_block}
UPCOMING MEETING
  Date:   {meeting_date}
  Medium: {meeting_medium}

TASK
Produce a meeting agenda as JSON with exactly these keys:
  "context_summary":      one short paragraph — where this relationship stands
                          and what this meeting should accomplish
  "agenda_items":         array of 1–7 strings, the conversation flow in order
  "discovery_questions":  array of 1–8 strings aimed at Layers 3–4
  "trust_building_moves": array of up to 5 give-first moves
Return only the JSON object."""


async def generate_agenda(
    contact: Contact,
    prior_interactions: list[Interaction],
    *,
    giving_status: str,
    open_action_texts: list[str],
    meeting_medium: str,
    meeting_date: str,
    objective: str | None = None,
) -> MeetingAgendaPlan:
    """
    Generate and validate a meeting agenda. Returns a validated
    MeetingAgendaPlan (NOT persisted). Raises AgendaGenerationError on failure.
    """
    system = _SYSTEM_PROMPT
    user = _build_user_prompt(
        contact, prior_interactions,
        giving_status=giving_status,
        open_action_texts=open_action_texts,
        objective=objective,
        meeting_medium=meeting_medium,
        meeting_date=meeting_date,
    )

    try:
        raw = await ai_service.complete_json(system, user, max_tokens=1000)
    except ai_service.AIServiceError as e:
        logger.warning(f"Agenda AI call failed: {e}")
        raise AgendaGenerationError(
            "The AI provider couldn't generate an agenda. "
            "Check your API key and provider setting, then try again."
        ) from e

    try:
        return MeetingAgendaPlan.model_validate(raw)
    except ValidationError as e:
        logger.warning(f"Agenda validation failed: {e}")
        raise AgendaGenerationError(
            "The AI returned a response that didn't match the expected agenda "
            "format. Try regenerating — this is usually transient."
        ) from e


def active_provider() -> str:
    return settings.llm_provider
