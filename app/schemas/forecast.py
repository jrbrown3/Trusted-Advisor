"""
app/schemas/forecast.py
───────────────────────
Validation contract for Feature J — Relationship Health Forecast.

Same gate as debrief and agenda. Two extra bits of defensive coercion:
the model sometimes returns a confidence as a percentage (85) rather than a
0–1 float, and occasionally an out-of-range value — both are normalised here.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RelationshipForecastResult(BaseModel):
    """Structured forecast for one relationship's trajectory."""

    trajectory: Literal["strengthening", "stable", "declining", "at_risk"] = Field(
        description="Where the relationship is heading on current evidence.",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Specific risks to trust (e.g. cold giving cadence, stalled at surface).",
    )
    opportunities: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Concrete openings to deepen the relationship.",
    )
    confidence_score: float = Field(
        ge=0.0, le=1.0,
        description="Model's confidence in this forecast, 0–1.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=900,
        description="Evidence-based reasoning behind the trajectory call.",
    )

    @field_validator("risk_flags", "opportunities")
    @classmethod
    def _strip_blanks(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _normalise_confidence(cls, v):
        """Accept 0–1 floats, or a 0–100 percentage, and clamp to [0, 1]."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.5  # neutral default if unparseable
        if f > 1.0:
            f = f / 100.0 if f <= 100.0 else 1.0
        return max(0.0, min(1.0, f))
