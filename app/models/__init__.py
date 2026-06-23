"""
app/models/__init__.py
──────────────────────
All ORM models in one place.

Table map → Framework feature:
  User            → Auth / session ownership
  Contact         → Feature 1  (Trust Scorecard)
  Interaction     → Feature 3  (Interaction Debrief Engine)
  DebriefDraft    → Feature A  (AI Debrief Auto-Summarization)
  InsightDelivery → Feature 7  (Insight Delivery Log)
  Action          → Feature 8  (Next Best Action Engine)
  MeetingAgenda   → Feature G  (AI Meeting Agenda Builder)
  RelationshipForecast → Feature J (AI Relationship Health Forecast)
"""

import enum
from datetime import datetime, date

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────

class DiscoveryLayer(enum.IntEnum):
    SURFACE    = 1   # Role, org structure, stated priorities
    OPERATIONAL = 2  # KPIs, pressures, budget authority
    STRATEGIC  = 3   # Long-term agenda, board pressures, competitive fears
    PERSONAL   = 4   # Career motivations, legacy, what keeps them up at night


class RiskLevel(str, enum.Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class TrustTrajectory(str, enum.Enum):
    STRENGTHENING = "strengthening"
    STABLE        = "stable"
    DECLINING     = "declining"
    AT_RISK       = "at_risk"


class ActionStatus(str, enum.Enum):
    PENDING   = "pending"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class ActionUrgency(str, enum.Enum):
    THIS_WEEK  = "this_week"
    THIS_MONTH = "this_month"
    WHEN_READY = "when_ready"


# ─────────────────────────────────────────────────────────────
# User  (auth)
# ─────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id:            Mapped[int]  = mapped_column(Integer, primary_key=True)
    username:      Mapped[str]  = mapped_column(String(64), unique=True, nullable=False)
    email:         Mapped[str]  = mapped_column(String(256), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active:     Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    contacts: Mapped[list["Contact"]] = relationship(back_populates="owner")


# ─────────────────────────────────────────────────────────────
# Contact  (Feature 1 — Trust Scorecard)
# ─────────────────────────────────────────────────────────────

class Contact(Base):
    __tablename__ = "contacts"

    id:           Mapped[int]  = mapped_column(Integer, primary_key=True)
    owner_id:     Mapped[int]  = mapped_column(ForeignKey("users.id"), nullable=False)

    # Identity
    full_name:    Mapped[str]  = mapped_column(String(128), nullable=False)
    title:        Mapped[str]  = mapped_column(String(128), nullable=True)
    organization: Mapped[str]  = mapped_column(String(128), nullable=True)
    linkedin_url: Mapped[str]  = mapped_column(String(512), nullable=True)
    notes:        Mapped[str]  = mapped_column(Text, nullable=True)

    # Trust Equation components (0.0–10.0 each, user-confirmed)
    # Trust = (credibility + reliability + intimacy) / self_orientation
    credibility:      Mapped[float] = mapped_column(Float, default=5.0)
    reliability:      Mapped[float] = mapped_column(Float, default=5.0)
    intimacy:         Mapped[float] = mapped_column(Float, default=5.0)
    self_orientation: Mapped[float] = mapped_column(Float, default=5.0)

    # Discovery progress
    deepest_layer_reached: Mapped[int] = mapped_column(
        Integer, default=DiscoveryLayer.SURFACE
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    owner:        Mapped["User"]              = relationship(back_populates="contacts")
    interactions: Mapped[list["Interaction"]] = relationship(back_populates="contact", cascade="all, delete-orphan")
    insights:     Mapped[list["InsightDelivery"]] = relationship(back_populates="contact", cascade="all, delete-orphan")
    actions:      Mapped[list["Action"]]      = relationship(back_populates="contact", cascade="all, delete-orphan")
    forecasts:    Mapped[list["RelationshipForecast"]] = relationship(back_populates="contact", cascade="all, delete-orphan")

    @property
    def trust_score(self) -> float:
        """
        Computed Trust Equation score.
        Clamped: self_orientation floor of 0.1 prevents division by zero.
        Returns value in range 0.0–30.0 (sum of numerator / denominator).
        """
        denom = max(self.self_orientation, 0.1)
        return round((self.credibility + self.reliability + self.intimacy) / denom, 2)


# ─────────────────────────────────────────────────────────────
# Interaction  (Feature 3 — Interaction Debrief Engine)
# ─────────────────────────────────────────────────────────────

class Interaction(Base):
    __tablename__ = "interactions"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False)

    # What happened
    interaction_date: Mapped[date]     = mapped_column(Date, nullable=False)
    medium:           Mapped[str]      = mapped_column(String(64), nullable=False)  # e.g. "coffee", "call", "email"
    raw_notes:        Mapped[str]      = mapped_column(Text, nullable=True)         # user's freeform notes
    duration_minutes: Mapped[int]      = mapped_column(Integer, nullable=True)

    # Discovery depth reached in this interaction
    layer_reached: Mapped[int] = mapped_column(Integer, default=DiscoveryLayer.SURFACE)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    contact:       Mapped["Contact"]      = relationship(back_populates="interactions")
    debrief_draft: Mapped["DebriefDraft"] = relationship(back_populates="interaction", uselist=False, cascade="all, delete-orphan")
    agenda:        Mapped["MeetingAgenda"] = relationship(back_populates="interaction", uselist=False, cascade="all, delete-orphan")


# ─────────────────────────────────────────────────────────────
# DebriefDraft  (Feature A — AI Debrief Auto-Summarization)
# ─────────────────────────────────────────────────────────────

class DebriefDraft(Base):
    """
    AI-generated debrief. Never auto-committed.
    Workflow: AI generates → user reviews → user confirms → status='confirmed'.
    """
    __tablename__ = "debrief_drafts"

    id:             Mapped[int] = mapped_column(Integer, primary_key=True)
    interaction_id: Mapped[int] = mapped_column(ForeignKey("interactions.id"), nullable=False)

    # AI output (validated by Pydantic before insert)
    key_themes:           Mapped[str]  = mapped_column(Text, nullable=True)   # JSON list
    trust_signals:        Mapped[str]  = mapped_column(Text, nullable=True)   # JSON list
    layer_reached:        Mapped[int]  = mapped_column(Integer, nullable=True)
    self_orientation_risk: Mapped[str] = mapped_column(String(16), nullable=True)
    suggested_next_action: Mapped[str] = mapped_column(Text, nullable=True)
    follow_up_date:       Mapped[date] = mapped_column(Date, nullable=True)

    # Confirmation workflow
    status:       Mapped[str]      = mapped_column(String(16), default="draft")  # draft | confirmed | dismissed
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    llm_provider: Mapped[str]      = mapped_column(String(16), nullable=True)   # which model generated this

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    interaction: Mapped["Interaction"] = relationship(back_populates="debrief_draft")


# ─────────────────────────────────────────────────────────────
# InsightDelivery  (Feature 7 — Insight Delivery Log)
# ─────────────────────────────────────────────────────────────

class InsightDelivery(Base):
    __tablename__ = "insight_deliveries"

    id:           Mapped[int]  = mapped_column(Integer, primary_key=True)
    contact_id:   Mapped[int]  = mapped_column(ForeignKey("contacts.id"), nullable=False)

    insight_text: Mapped[str]  = mapped_column(Text, nullable=False)
    source:       Mapped[str]  = mapped_column(String(256), nullable=True)   # article, report, etc.
    delivered_on: Mapped[date] = mapped_column(Date, nullable=False)
    medium:       Mapped[str]  = mapped_column(String(64), nullable=True)    # email, call, in-person

    # Did it land?
    response_noted: Mapped[str] = mapped_column(Text, nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    contact: Mapped["Contact"] = relationship(back_populates="insights")


# ─────────────────────────────────────────────────────────────
# Action  (Feature 8 — Next Best Action Engine)
# ─────────────────────────────────────────────────────────────

class Action(Base):
    __tablename__ = "actions"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id:  Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False)

    action_text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale:   Mapped[str] = mapped_column(Text, nullable=True)     # why the AI suggested this
    urgency:     Mapped[str] = mapped_column(String(16), default=ActionUrgency.WHEN_READY)
    status:      Mapped[str] = mapped_column(String(16), default=ActionStatus.PENDING)

    due_date:    Mapped[date]     = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # AI provenance
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_provider: Mapped[str]  = mapped_column(String(16), nullable=True)

    contact: Mapped["Contact"] = relationship(back_populates="actions")


# ─────────────────────────────────────────────────────────────
# MeetingAgenda  (Feature G — AI Meeting Agenda Builder)
# ─────────────────────────────────────────────────────────────

class MeetingAgenda(Base):
    __tablename__ = "meeting_agendas"

    id:             Mapped[int]  = mapped_column(Integer, primary_key=True)
    interaction_id: Mapped[int]  = mapped_column(ForeignKey("interactions.id"), nullable=False)

    # AI output
    agenda_items:         Mapped[str] = mapped_column(Text, nullable=True)   # JSON list
    discovery_questions:  Mapped[str] = mapped_column(Text, nullable=True)   # JSON list
    trust_building_moves: Mapped[str] = mapped_column(Text, nullable=True)   # JSON list
    context_summary:      Mapped[str] = mapped_column(Text, nullable=True)

    status:       Mapped[str]      = mapped_column(String(16), default="draft")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    llm_provider: Mapped[str]      = mapped_column(String(16), nullable=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    interaction: Mapped["Interaction"] = relationship(back_populates="agenda")


# ─────────────────────────────────────────────────────────────
# RelationshipForecast  (Feature J — Relationship Health Forecast)
# ─────────────────────────────────────────────────────────────

class RelationshipForecast(Base):
    __tablename__ = "relationship_forecasts"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False)

    # AI output (validated, user-confirmed before storage)
    trajectory:      Mapped[str]   = mapped_column(String(24), nullable=True)  # TrustTrajectory enum
    risk_flags:      Mapped[str]   = mapped_column(Text, nullable=True)        # JSON list
    opportunities:   Mapped[str]   = mapped_column(Text, nullable=True)        # JSON list
    confidence_score: Mapped[float] = mapped_column(Float, nullable=True)      # 0.0–1.0
    rationale:       Mapped[str]   = mapped_column(Text, nullable=True)

    status:       Mapped[str]      = mapped_column(String(16), default="draft")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    llm_provider: Mapped[str]      = mapped_column(String(16), nullable=True)

    # Forecast window
    forecast_as_of: Mapped[date]   = mapped_column(Date, nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    contact: Mapped["Contact"] = relationship(back_populates="forecasts")
