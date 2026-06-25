# Trusted Advisor — Relationship Intelligence

A web application that operationalizes the **Trusted Advisor framework** (Maister's Trust Equation) for managing high-value strategic partnerships. Built for professionals who need to move beyond CRM pipeline logic and into depth-based relationship architecture.

---

## The framework

Every feature maps back to one equation:

```
Trust = (Credibility + Reliability + Intimacy) / Self-Orientation
```

And four discovery layers that represent how deeply a relationship has developed:

| Layer | What it means |
|-------|---------------|
| 1 — Surface | Role, org structure, stated priorities |
| 2 — Operational | KPIs, pressures, team dynamics, budget authority |
| 3 — Strategic | Long-term agenda, board pressures, competitive fears |
| 4 — Personal | Career motivations, legacy intent, what keeps them up at night |

The app tracks trust dimensions, interaction depth, giving cadence, and AI-generated signals — all routed back to these two frameworks.

---

## Features

### Core
| # | Feature | What it does |
|---|---------|-------------|
| 1 | **Contact Trust Scorecard** | Evidence-anchored scoring across all four Trust Equation dimensions. Each score is defended by a diagnostic question, not a slider. |
| 3 | **Interaction Debrief Engine** | Log an interaction, generate an AI debrief, review and confirm. Draft-then-confirm — nothing auto-commits. |
| 7 | **Insight Delivery Log** | Track value delivered to each contact. Giving cadence (active / cooling / cold) is the antidote to high self-orientation. |
| 8 | **Next Best Action Engine** | Prioritized worklist across the whole portfolio — scored by time-pressure, reliability-at-stake, and cadence leverage. |

### AI features
| # | Feature | What it does |
|---|---------|-------------|
| A | **Debrief Auto-Summarization** | AI extracts themes, trust signals, discovery layer reached, self-orientation risk, and a suggested next action from raw meeting notes. |
| G | **Meeting Agenda Builder** | AI drafts a discovery-focused agenda from the full relationship history. Aimed at Layers 3–4; structured around give-first moves. |
| J | **Relationship Health Forecast** | AI projects trajectory (strengthening / stable / declining / at-risk) with risk flags and opportunities. Timestamp-based caching — only regenerates when the relationship has changed. |

All AI features follow the same pattern: **AI suggests, user confirms**. No AI output auto-commits to the database.

---

## Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (async) |
| ORM | SQLAlchemy 2.0 async |
| Database | SQLite (dev) → PostgreSQL path (one connection-string swap) |
| Frontend | Jinja2 + HTMX (server-rendered, no JS framework) |
| Auth | PyJWT (HS256) + bcrypt, httpOnly session cookies |
| AI | Anthropic Claude API (`claude-sonnet-4-6`) — switchable to OpenAI via config |
| Validation | Pydantic v2 (all AI output gated before any DB write) |
| Server | Uvicorn (bare — no Rust build deps) |

---

## Getting started

### Requirements
- Python 3.11+
- An Anthropic API key (or OpenAI key if switching providers)

### Install

```bash
git clone https://github.com/jrbrown3/Trusted-Advisor.git
cd Trusted-Advisor

# On ARM64 Windows (Snapdragon): use --only-binary to avoid Rust builds
pip install -r requirements.txt --only-binary=:all:

# Standard install on other platforms
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
APP_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(48))">
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=claude
```

API keys can also be managed from the in-app **Settings** page after first login.

### First run

```bash
python run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On a fresh install with no database, you'll land on the **first-run setup page** to create the first account. You'll be signed in automatically and land on the dashboard.

> **If you have an existing database from a previous session**, delete it before running to trigger the clean setup experience:
> ```bash
> rm trusted_advisor.db
> ```

---

## Project structure

```
app/
├── config.py              # Pydantic Settings (all env config)
├── database.py            # Async engine, session factory
├── dependencies.py        # Auth dependency: get_current_user
├── main.py                # FastAPI app, router registration, exception handlers
├── models/
│   └── __init__.py        # All ORM models (Contact, Interaction, Action, …)
├── routers/
│   ├── auth.py            # Login / logout
│   ├── setup.py           # First-run provisioning
│   ├── contacts.py        # Contact CRUD + forecast routes
│   ├── interactions.py    # Interaction CRUD + debrief + agenda lifecycle
│   ├── insights.py        # Insight Delivery Log
│   ├── actions.py         # Next Best Action worklist
│   ├── dashboard.py       # Portfolio dashboard (GET /)
│   └── settings_router.py # AI config + account management
├── schemas/
│   ├── debrief.py         # Pydantic: DebriefSummary
│   ├── agenda.py          # Pydantic: MeetingAgendaPlan
│   └── forecast.py        # Pydantic: RelationshipForecastResult
├── services/
│   ├── ai_service.py      # Single AI integration point (Claude / OpenAI)
│   ├── security.py        # bcrypt + PyJWT helpers
│   ├── scoring.py         # Trust Equation computation + banding
│   ├── debrief_service.py # AI debrief generation
│   ├── agenda_service.py  # AI agenda generation
│   ├── forecast_service.py# AI forecast + cache freshness logic
│   ├── insights_service.py# Giving-cadence calculation
│   ├── nba_service.py     # Action scoring + worklist builder
│   └── dashboard_service.py # Portfolio aggregation
├── static/
│   ├── css/main.css       # Design system (navy/blue brand palette, no framework)
│   └── img/               # Logo assets (logo.png, mark.png, favicon.png)
└── templates/
    ├── base.html           # App shell (sidebar, mobile nav drawer)
    ├── auth_base.html      # Minimal layout for login/setup
    └── pages/             # One template per route
```

---

## Architecture decisions

**AI suggests, user confirms — always.**  
All AI output routes through `ai_service.py`, is validated by a Pydantic schema, and is stored as a `draft` before any side effects occur. Confirming a draft is the only path to DB writes from AI output.

**Single AI integration point.**  
`ai_service.py` is the only file that touches the LLM provider. Switching from Claude to OpenAI is a one-line config change (`LLM_PROVIDER=openai`).

**Timestamp-based forecast caching.**  
Forecasts are expensive. Rather than storing a fingerprint, the cache is timestamp-based: a forecast is reused if it was generated *after* the newest relationship event and is younger than 14 days. Zero new schema columns.

**No Alembic yet.**  
The schema is created with `Base.metadata.create_all()` on startup. For production / PostgreSQL migration, Alembic is the natural next step.

**ARM64 Windows compatibility.**  
Tested on Snapdragon (aarch64 Windows). Pinned `pydantic>=2.10.6` (first version with `win_arm64` wheels), bare `uvicorn` (no Rust), and replaced `passlib` with direct `bcrypt`. Install: `pip install -r requirements.txt --only-binary=:all:`.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_SECRET_KEY` | `change-me` | Signs session JWTs. Must be strong in production. |
| `APP_DEBUG` | `true` | Controls HTTPS-only cookie flag. Set `false` in prod. |
| `APP_HOST` | `127.0.0.1` | Bind address |
| `APP_PORT` | `8000` | Port |
| `DATABASE_URL` | `sqlite+aiosqlite:///./trusted_advisor.db` | Swap to `postgresql+asyncpg://...` for Postgres |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Session lifetime (8 hours) |
| `LLM_PROVIDER` | `claude` | `claude` or `openai` |
| `LLM_MODEL_CLAUDE` | `claude-sonnet-4-6` | Claude model string |
| `LLM_MODEL_OPENAI` | `gpt-4o` | OpenAI model string |
| `ANTHROPIC_API_KEY` | | Required for Claude features |
| `OPENAI_API_KEY` | | Required if `LLM_PROVIDER=openai` |

---

## License

MIT
