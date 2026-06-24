"""
app/services/scoring.py
───────────────────────
Evidence-anchored Trust Equation scoring.

The Trust Equation is a *diagnostic*, not a self-assessment. A score you can
inflate tells you nothing. So instead of asking the user to set a 0–10 slider
directly, each dimension is anchored to a concrete diagnostic question whose
answers map to defensible scores.

This module is the single source of truth for:
  - the diagnostic questions and their answer options
  - the score each answer maps to
  - the Trust Equation computation itself

Both the form (rendering) and the routers (computing/saving) import from here,
so the questions and the scoring can never drift apart.

    Trust = (Credibility + Reliability + Intimacy) / Self-Orientation

Note the asymmetry: for C, R, and I, higher is better. For Self-Orientation,
LOWER is better — it sits in the denominator. The questions are worded so the
user is never "scoring themselves high" on a good thing; they're describing
observable evidence.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AnchorOption:
    """A single answer option anchored to a score."""
    value: float          # the score this answer maps to (0–10)
    label: str            # what the user reads and selects


@dataclass(frozen=True)
class Dimension:
    """One Trust Equation dimension and its diagnostic question."""
    key: str              # matches the Contact model column name
    name: str             # display name
    blurb: str            # one-line reminder of what this dimension measures
    question: str         # the diagnostic question
    options: list[AnchorOption]
    lower_is_better: bool = False   # True only for self_orientation


# ─────────────────────────────────────────────────────────────
# CREDIBILITY — do they trust what you SAY?
# ─────────────────────────────────────────────────────────────
CREDIBILITY = Dimension(
    key="credibility",
    name="Credibility",
    blurb="Domain authority — do they trust what you say?",
    question="When this person needs an expert view in your domain, what do they actually do?",
    options=[
        AnchorOption(10.0, "They seek me out by name and act on my input directly"),
        AnchorOption(7.5,  "They include me among a few trusted voices they rely on"),
        AnchorOption(5.0,  "They listen, but verify elsewhere before acting"),
        AnchorOption(2.5,  "They treat my input as one data point among many"),
        AnchorOption(0.0,  "They haven't yet sought my expertise"),
    ],
)

# ─────────────────────────────────────────────────────────────
# RELIABILITY — do they trust what you DO?
# ─────────────────────────────────────────────────────────────
RELIABILITY = Dimension(
    key="reliability",
    name="Reliability",
    blurb="Consistency of follow-through — do they trust what you do?",
    question="Across your last several commitments to this person, what's the track record?",
    options=[
        AnchorOption(10.0, "Every commitment met, often early, no reminders needed"),
        AnchorOption(7.5,  "Consistently met, occasionally needs a nudge"),
        AnchorOption(5.0,  "Mostly met, with some visible slippage"),
        AnchorOption(2.5,  "Mixed — they'd hesitate to count on me"),
        AnchorOption(0.0,  "Too new to have any track record"),
    ],
)

# ─────────────────────────────────────────────────────────────
# INTIMACY — do they trust you with SENSITIVE things?
# ─────────────────────────────────────────────────────────────
INTIMACY = Dimension(
    key="intimacy",
    name="Intimacy",
    blurb="Psychological safety — do they trust you with what's sensitive?",
    question="What's the most unguarded thing this person has shared with you?",
    options=[
        AnchorOption(10.0, "Personal fears or career anxieties they'd hide from their board"),
        AnchorOption(7.5,  "Strategic concerns they don't share widely"),
        AnchorOption(5.0,  "Honest operational frustrations"),
        AnchorOption(2.5,  "Mostly professional, occasionally candid"),
        AnchorOption(0.0,  "Purely transactional — surface level only"),
    ],
)

# ─────────────────────────────────────────────────────────────
# SELF-ORIENTATION — the denominator. LOWER is better.
# This is the silent deal-killer and the one people most underrate
# in themselves. The question forces an honest look at whose agenda
# the relationship actually serves.
# ─────────────────────────────────────────────────────────────
SELF_ORIENTATION = Dimension(
    key="self_orientation",
    name="Self-Orientation",
    blurb="Perceived agenda — the silent deal-killer. Lower is better.",
    question="Across your last 3 interactions, where did the focus genuinely sit?",
    options=[
        AnchorOption(1.0,  "Almost entirely on their world — their problems, their goals"),
        AnchorOption(2.5,  "Mostly them, with some of my agenda surfacing"),
        AnchorOption(5.0,  "Roughly balanced between their needs and mine"),
        AnchorOption(7.5,  "Often steered toward what I'm offering"),
        AnchorOption(10.0, "Primarily about advancing my own goals"),
    ],
    lower_is_better=True,
)


# Ordered list used by templates and routers
DIMENSIONS: list[Dimension] = [CREDIBILITY, RELIABILITY, INTIMACY, SELF_ORIENTATION]
DIMENSIONS_BY_KEY: dict[str, Dimension] = {d.key: d for d in DIMENSIONS}


# ─────────────────────────────────────────────────────────────
# Computation
# ─────────────────────────────────────────────────────────────

# Self-orientation can never be zero (division by zero) — floor it.
_SELF_ORIENTATION_FLOOR = 0.1


def compute_trust_score(
    credibility: float,
    reliability: float,
    intimacy: float,
    self_orientation: float,
) -> float:
    """
    Trust = (Credibility + Reliability + Intimacy) / Self-Orientation

    Returns a value roughly in the range 0.3–30.0.
    Mirrors Contact.trust_score on the model — kept here so the routers can
    compute a *preview* before anything is written to the DB.
    """
    denom = max(self_orientation, _SELF_ORIENTATION_FLOOR)
    return round((credibility + reliability + intimacy) / denom, 2)


def score_band(score: float) -> str:
    """
    Map a numeric trust score to a band for UI colour-coding.
    Returns one of: "high" | "mid" | "low".

    Bands are calibrated against the equation's range. With balanced inputs
    (self-orientation ~2.5–5), scores above ~6 reflect genuinely strong trust;
    below ~3 signals a relationship that is transactional or at risk.
    """
    if score >= 6.0:
        return "high"
    if score >= 3.0:
        return "mid"
    return "low"


def closest_option_value(dimension_key: str, stored_value: float) -> float:
    """
    Given a stored score, return the anchor option value it matches most
    closely. Used to pre-select the right radio button when editing a contact
    whose score was set previously.
    """
    dim = DIMENSIONS_BY_KEY[dimension_key]
    return min(dim.options, key=lambda o: abs(o.value - stored_value)).value
