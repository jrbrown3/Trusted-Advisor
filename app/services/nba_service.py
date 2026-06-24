"""
app/services/nba_service.py
───────────────────────────
Feature 8 — Next Best Action Engine.

This is a rule-based prioritizer, not an AI feature. It takes the pending
actions accumulating across all contacts (mostly from confirmed debriefs)
and sequences them into a single worklist.

"Next BEST" is a framework judgement, not just "soonest due." The score
weights three things, in order of trust impact:

  1. Time pressure        — overdue / due-soon / urgency flag
  2. Reliability at stake  — an OVERDUE commitment to a HIGH-TRUST contact is
                             the most damaging miss, because reliability is
                             exactly the trust you've already earned. The
                             framework treats follow-through as a core trust
                             driver, so protecting it ranks high.
  3. Re-engagement leverage — when a contact's giving cadence has gone cold,
                             acting now re-warms the relationship and lowers
                             perceived self-orientation.

Every scored action carries a human-readable `reason` (its top-weighted
factor) so the worklist explains itself rather than ranking opaquely.
"""

from dataclasses import dataclass
from datetime import date, datetime

from app.models import Action, Contact, ActionUrgency


# Band thresholds on the final score.
_DO_NOW = 45
_THIS_WEEK = 22


@dataclass
class ScoredAction:
    action: Action
    contact: Contact
    score: float
    band: str            # "do_now" | "this_week" | "when_ready"
    reason: str          # the top-weighted driver, shown in the UI


def _band(score: float) -> str:
    if score >= _DO_NOW:
        return "do_now"
    if score >= _THIS_WEEK:
        return "this_week"
    return "when_ready"


def score_action(
    action: Action,
    contact: Contact,
    *,
    trust_band: str,            # "high" | "mid" | "low"  (from scoring.score_band)
    giving_status: str,         # "active" | "cooling" | "cold" | "none"
    today: date | None = None,
) -> ScoredAction:
    """Score a single pending action. Pure function — easy to unit-test."""
    today = today or date.today()
    score = 0.0
    # Each reason is (weight, text); the highest-weight reason is shown.
    reasons: list[tuple[float, str]] = []

    # 1) Time pressure from an explicit due date.
    if action.due_date:
        days = (action.due_date - today).days
        if days < 0:
            score += 50
            reasons.append((50, f"Overdue by {-days}d — following through protects your reliability"))
        elif days == 0:
            score += 42
            reasons.append((42, "Due today"))
        elif days <= 3:
            score += 35
            reasons.append((35, f"Due in {days}d"))
        elif days <= 7:
            score += 25
            reasons.append((25, f"Due in {days}d"))
        else:
            score += 8
            reasons.append((8, f"Due {action.due_date.strftime('%b %d')}"))

    # 2) Urgency flag (independent of an explicit date).
    urgency_pts = {
        ActionUrgency.THIS_WEEK.value: 22,
        ActionUrgency.THIS_MONTH.value: 12,
        ActionUrgency.WHEN_READY.value: 3,
    }
    up = urgency_pts.get(action.urgency, 3)
    score += up
    if up >= 22:
        reasons.append((up, "Flagged for this week"))

    # 3) Reliability protection: overdue commitment to a high-trust contact.
    if action.due_date and (action.due_date - today).days < 0 and trust_band == "high":
        score += 15
        reasons.append((16, "Overdue commitment to a high-trust contact — reliability is at stake"))

    # 4) Re-engagement leverage from a cold/cooling giving cadence.
    if giving_status == "cold":
        score += 12
        reasons.append((12, "Giving cadence is cold — a value-add move re-warms it"))
    elif giving_status == "cooling":
        score += 6
        reasons.append((6, "Giving cadence cooling"))

    # 5) Staleness: long-pending actions resurface so they don't slip.
    if action.created_at:
        created = action.created_at.date() if isinstance(action.created_at, datetime) else action.created_at
        age_days = (today - created).days
        if age_days > 14:
            bump = min((age_days // 14) * 2, 10)
            score += bump
            reasons.append((bump, f"Pending {age_days}d — don't let it slip"))

    reason = max(reasons, key=lambda r: r[0])[1] if reasons else "No urgency signals yet"
    return ScoredAction(action=action, contact=contact, score=round(score, 1),
                        band=_band(score), reason=reason)


def build_worklist(
    scored: list[ScoredAction],
) -> dict[str, list[ScoredAction]]:
    """
    Group scored actions into ordered bands, each sorted by score descending.
    Returns a dict with keys do_now / this_week / when_ready.
    """
    out: dict[str, list[ScoredAction]] = {"do_now": [], "this_week": [], "when_ready": []}
    for sa in scored:
        out[sa.band].append(sa)
    for band in out:
        out[band].sort(key=lambda s: s.score, reverse=True)
    return out
