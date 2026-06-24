"""
app/services/dashboard_service.py
─────────────────────────────────
Phase 8 — Dashboard aggregation.

Synthesises the whole portfolio into one view that answers a single question:
"where should I focus my relationship energy right now?"

This service reuses the logic from earlier phases rather than reinventing it:
  - scoring.score_band         (trust banding)
  - insights_service.giving_signal (giving cadence)
  - nba_service.score_action   (action prioritisation)

It takes contacts with .insights / .actions / .forecasts / .interactions
already eager-loaded, and returns a fully-computed DashboardData.
"""

from dataclasses import dataclass, field
from datetime import date

from app.models import Contact, ActionStatus
from app.services import scoring, insights_service, nba_service


@dataclass
class PortfolioRow:
    contact: Contact
    score: float
    band: str
    trajectory: str | None      # latest non-dismissed forecast trajectory, or None
    cadence: str                # active | cooling | cold | none
    layer: int
    open_count: int


@dataclass
class AttentionItem:
    contact: Contact
    reasons: list[str]
    severity: int               # higher = more urgent (sorts to top)


@dataclass
class DashboardData:
    total_contacts: int = 0
    avg_trust: float = 0.0
    open_actions: int = 0
    overdue_actions: int = 0
    needs_attention: int = 0
    do_now: list = field(default_factory=list)         # list[ScoredAction]
    attention: list[AttentionItem] = field(default_factory=list)
    portfolio: list[PortfolioRow] = field(default_factory=list)


def _latest_trajectory(contact: Contact) -> str | None:
    """Trajectory from the most recent non-dismissed forecast, if any."""
    forecasts = [f for f in contact.forecasts if f.status != "dismissed"]
    if not forecasts:
        return None
    latest = max(forecasts, key=lambda f: f.created_at or date.min)
    return latest.trajectory


def build_dashboard(contacts: list[Contact], *, today: date | None = None) -> DashboardData:
    today = today or date.today()
    data = DashboardData(total_contacts=len(contacts))

    if not contacts:
        return data

    trust_sum = 0.0
    all_scored_actions = []

    for c in contacts:
        score = c.trust_score
        band = scoring.score_band(score)
        trust_sum += score

        giving = insights_service.giving_signal(
            [ins.delivered_on for ins in c.insights], today=today
        )

        open_actions = [a for a in c.actions if a.status == ActionStatus.PENDING]
        overdue = [a for a in open_actions if a.due_date and a.due_date < today]
        data.open_actions += len(open_actions)
        data.overdue_actions += len(overdue)

        trajectory = _latest_trajectory(c)

        # Score this contact's pending actions for the global "do now" list.
        for a in open_actions:
            all_scored_actions.append(
                nba_service.score_action(
                    a, c, trust_band=band, giving_status=giving.status, today=today
                )
            )

        # Portfolio row.
        data.portfolio.append(PortfolioRow(
            contact=c, score=score, band=band, trajectory=trajectory,
            cadence=giving.status, layer=c.deepest_layer_reached,
            open_count=len(open_actions),
        ))

        # Attention reasons — where trust is eroding or stalling.
        reasons: list[str] = []
        severity = 0
        if trajectory == "at_risk":
            reasons.append("Forecast: at risk"); severity += 4
        elif trajectory == "declining":
            reasons.append("Forecast: declining"); severity += 3
        if overdue:
            n = len(overdue)
            reasons.append(f"{n} overdue commitment{'s' if n > 1 else ''}")
            severity += 3  # reliability erosion is serious
        if giving.status == "cold":
            reasons.append("Giving cadence cold"); severity += 2
        elif giving.status == "cooling":
            reasons.append("Giving cadence cooling"); severity += 1
        if c.deepest_layer_reached <= 1 and len(c.interactions) >= 3:
            reasons.append(f"Stalled at surface after {len(c.interactions)} touchpoints")
            severity += 1

        if reasons:
            # Tie-break by trust: protect higher-trust relationships first.
            data.attention.append(AttentionItem(
                contact=c, reasons=reasons, severity=severity * 100 + int(score),
            ))

    data.avg_trust = round(trust_sum / len(contacts), 1)
    data.needs_attention = len(data.attention)

    # Sort portfolio by trust score (highest first).
    data.portfolio.sort(key=lambda r: r.score, reverse=True)

    # Attention: most urgent first.
    data.attention.sort(key=lambda i: i.severity, reverse=True)

    # Do-now actions across the whole portfolio, top-priority first.
    worklist = nba_service.build_worklist(all_scored_actions)
    data.do_now = worklist["do_now"][:6]

    return data
