"""
app/schemas/agenda.py
─────────────────────
Validation contract for Feature G — AI Meeting Agenda Builder.

Same gate as the debrief: the LLM returns free-form JSON, this schema
enforces shape before anything is stored as a MeetingAgenda draft.

The structure encodes the framework: an agenda isn't a list of topics to
cover, it's a plan to go DEEPER (discovery_questions aimed at Layer 3-4)
while LEADING WITH VALUE (trust_building_moves), keeping self-orientation low.
"""

from pydantic import BaseModel, Field, field_validator


class MeetingAgendaPlan(BaseModel):
    """Structured agenda for an upcoming meeting."""

    context_summary: str = Field(
        min_length=1,
        max_length=800,
        description="Where the relationship stands and the goal for this meeting.",
    )
    agenda_items: list[str] = Field(
        min_length=1,
        max_length=7,
        description="The flow of the conversation — what to cover, in order.",
    )
    discovery_questions: list[str] = Field(
        min_length=1,
        max_length=8,
        description="Questions that push past surface toward strategic (3) and personal (4) layers.",
    )
    trust_building_moves: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Specific give-first moves: insight to offer, value to bring, candor to model.",
    )

    @field_validator("agenda_items", "discovery_questions", "trust_building_moves")
    @classmethod
    def _strip_blanks(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]
