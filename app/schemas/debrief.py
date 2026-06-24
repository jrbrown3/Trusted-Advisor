"""
app/schemas/debrief.py
──────────────────────
Validation contract for Feature A — AI Debrief Auto-Summarization.

The LLM returns free-form JSON. This schema enforces shape and types before
anything is stored as a DebriefDraft. If the model hallucinates a field,
returns "deep" instead of 4, or omits a required key, validation raises and
the router shows an error instead of persisting garbage.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DebriefSummary(BaseModel):
    """Structured debrief extracted from interaction notes."""

    key_themes: list[str] = Field(
        min_length=1,
        max_length=5,
        description="The main topics that surfaced in the interaction (1–5).",
    )
    trust_signals: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Specific moments that built or eroded trust.",
    )
    layer_reached: Literal[1, 2, 3, 4] = Field(
        description="Deepest discovery layer reached: 1 surface … 4 personal.",
    )
    self_orientation_risk: Literal["low", "medium", "high"] = Field(
        description="Did the interaction feel transactional / about the advisor's agenda?",
    )
    suggested_next_action: str = Field(
        min_length=1,
        max_length=300,
        description="The single most important follow-up move.",
    )
    follow_up_date: date | None = Field(
        default=None,
        description="Suggested date for the next touchpoint, or null.",
    )

    @field_validator("key_themes", "trust_signals")
    @classmethod
    def _strip_blanks(cls, v: list[str]) -> list[str]:
        """Drop empty / whitespace-only strings the model might emit."""
        return [s.strip() for s in v if s and s.strip()]

    # Pydantic will coerce an ISO date string ("2026-07-01") to a date.
    # If the model returns an empty string for the date, treat it as None.
    @field_validator("follow_up_date", mode="before")
    @classmethod
    def _empty_date_to_none(cls, v):
        if v in ("", "null", "none", None):
            return None
        return v
