"""
app/services/insights_service.py
────────────────────────────────
Feature 7 — Insight Delivery Log.

Why this feature exists in the framework:

In the Trust Equation, Self-Orientation is the denominator — the silent
deal-killer. The single most reliable way to *lower* perceived self-orientation
is to demonstrably give value without asking for anything back. Delivered
insights are the evidence of that giving.

So this log isn't just a CRUD list. Its job is to surface the *giving cadence*
for each relationship: how recently, and how often, you've delivered value.
A high-value contact you haven't given anything to in months is a
self-orientation risk waiting to happen — when you finally reach out, the ask
will land cold.
"""

from dataclasses import dataclass
from datetime import date


# Cadence thresholds (days since last delivered insight).
_ACTIVE_DAYS = 30
_COOLING_DAYS = 90


@dataclass(frozen=True)
class GivingSignal:
    """A read on how warm the giving cadence is for one contact."""
    status: str          # "active" | "cooling" | "cold" | "none"
    label: str           # human-readable
    days_since: int | None
    total_delivered: int


def days_since(d: date | None, *, today: date | None = None) -> int | None:
    """Whole days between a date and today. None if no date."""
    if d is None:
        return None
    today = today or date.today()
    return (today - d).days


def giving_signal(
    delivered_dates: list[date],
    *,
    today: date | None = None,
) -> GivingSignal:
    """
    Compute the giving cadence from a contact's delivered-insight dates.

    Bands:
      active  — delivered within the last 30 days
      cooling — 30–90 days since last delivery
      cold    — over 90 days since last delivery
      none    — never delivered an insight to this contact
    """
    total = len(delivered_dates)
    if total == 0:
        return GivingSignal("none", "No insights delivered", None, 0)

    most_recent = max(delivered_dates)
    gap = days_since(most_recent, today=today) or 0

    if gap <= _ACTIVE_DAYS:
        status, label = "active", "Giving cadence active"
    elif gap <= _COOLING_DAYS:
        status, label = "cooling", "Cooling — give value soon"
    else:
        status, label = "cold", "Cold — overdue to give"

    return GivingSignal(status, label, gap, total)
