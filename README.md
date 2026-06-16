# WTC Abuja / Churchgate Sales Dashboard — Backend

FastAPI service that ingests deal data from **Freshsales CRM** into **Supabase
(Postgres)** and exposes an authenticated API for the sales dashboard. It keeps a
queryable mirror of pipelines, stages, owners and deals, and records an immutable
event log of stage/owner changes for funnel and velocity analytics.

---

## Tech stack

| Concern | Choice |
|---|---|
| Language / runtime | Python 3.12, [`uv`](https://docs.astral.sh/uv/) for deps & venv |
| Web framework | FastAPI (`fastapi[standard]`, run via the `fastapi` CLI) |
| ORM / models | SQLModel (async SQLAlchemy 2.0) + `asyncpg` |
| Migrations | Alembic (async engine) |
| Config | pydantic-settings (12-factor, env-driven) |
| Scheduling | APScheduler (`AsyncIOScheduler`) + Postgres advisory locks |
| HTTP client | HTTPX (async) + tenacity (retry/backoff) + token-bucket rate limiter |
| Auth | bcrypt password hashing + PyJWT (access/refresh tokens) |
| Logging | structlog (JSON in prod, console in dev) |
| Tests | pytest + pytest-asyncio + respx (Freshsales mocks) |
| Lint / types | ruff + ty |
| Deploy | Railway (Dockerfile) |

---

## Architecture

The service has three data paths into Postgres and one path out to clients:

```
                        ┌─────────────────────────────────────────────┐
                        │                Freshsales CRM                │
                        └─────────────────────────────────────────────┘
                           │ REST (pull)          │ webhook (push)
                           ▼                       ▼
   ┌───────────────────────────────┐   ┌──────────────────────────────┐
   │ APScheduler jobs (advisory-    │   │ POST /webhooks/freshsales/deal│
   │ lock guarded)                  │   │ (HMAC-verified)               │
   │  • reference_sync_job (daily)  │   │                               │
   │  • deal_sync_job (every 20m)   │   │  ingest → diff → event + snap │
   └───────────────────────────────┘   └──────────────────────────────┘
                           │                       │
                           ▼                       ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │                          Supabase / Postgres                       │
   │  reference: pipelines, stages, owners                              │
   │  state:     deals_snapshot, tasks_snapshot, email_activity         │
   │  history:   deal_events (append-only), pipeline_daily_snapshot     │
   │  auth:      dashboard_users                                        │
   └──────────────────────────────────────────────────────────────────┘
                           │
                           ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │           FastAPI API  (/api/v1, JWT cookie + bearer)              │
   │  auth (login/refresh/me/logout) · admin (sync, user mgmt)         │
   └──────────────────────────────────────────────────────────────────┘
```

### Layering

Requests flow top-down; each layer only depends on the one below it.

```
api/        HTTP edge — routers, request/response schemas, auth dependencies
  │
services/   business logic — sync orchestration, change detection, ingest
  │
repositories/  data access — thin async functions over SQLModel
  │
models/     SQLModel table definitions (the schema)

core/       cross-cutting: config, db engine, logging, scheduler, security
freshsales/ outbound CRM integration: client, endpoint registry, parsing
jobs/       APScheduler entrypoints that wire services to the scheduler
```

**Why this shape:** the API never builds SQL, services never touch HTTP, and the
Freshsales-specific quirks (Suite API host/paths, `MM-DD-YYYY` timestamps, rate
limits, `cf_*` custom fields) are all quarantined in `freshsales/`. Swapping the
CRM or the transport touches one package.

---

## Project structure

```
backend/
├── app/
│   ├── main.py                 # create_app(), lifespan (startup sync + scheduler), CORS
│   ├── api/
│   │   ├── dependencies.py      # SessionDep, get_current_user, require_role, resolver
│   │   └── v1/
│   │       ├── router.py        # mounts auth + admin under /api/v1
│   │       └── endpoints/
│   │           ├── auth.py      # login, me, refresh, logout
│   │           ├── admin.py     # manual sync triggers, user management
│   │           ├── health.py    # /healthz (liveness), /readyz (db check)
│   │           └── webhooks.py  # /webhooks/freshsales/deal (no /api/v1 prefix)
│   ├── core/
│   │   ├── config.py            # pydantic Settings (env-driven)
│   │   ├── database.py          # async engine, session_scope, ping_database
│   │   ├── logging.py           # structlog setup
│   │   ├── scheduler.py         # AsyncIOScheduler + pg_try_advisory_lock wrapper
│   │   └── security.py          # bcrypt hashing, JWT create/decode
│   ├── freshsales/
│   │   ├── client.py            # async HTTPX client, rate limiter, retry/backoff
│   │   ├── endpoints.py         # Freshsales Suite API path registry (/crm/sales/api/...)
│   │   └── parsing.py           # timestamp/TZ normalization, cf_* split, resolver
│   ├── models/                  # SQLModel tables (one file per table)
│   ├── repositories/            # async data-access functions
│   ├── schemas/                 # Pydantic request/response models (+ envelopes)
│   ├── services/
│   │   ├── reference_sync.py    # pipelines + stages + owners sync
│   │   ├── deal_sync.py         # paginated deal snapshot upsert
│   │   ├── webhook_ingest.py    # webhook → change detection → event + snapshot
│   │   └── change_detection.py  # snapshot diff → event types
│   └── jobs/tasks.py            # APScheduler job functions
├── alembic/                     # migrations (env.py uses create_async_engine)
├── tests/                       # unit/ + integration/ (respx-mocked Freshsales)
├── scripts/
│   ├── run.sh                   # dev bootstrap (deps, migrate, fastapi dev)
│   └── seed_user.py             # bootstrap a superadmin (bcrypt + getpass)
├── Dockerfile                   # production image
├── railway.toml                 # Railway build + start + healthcheck
├── docker-compose.yml           # local postgres + backend
└── pyproject.toml               # deps, ruff, pytest, fastapi entrypoint
```

---

## Data model

**Reference data** (synced from Freshsales, rarely changes):
- `pipelines` — pipeline + `business_line`; `view_id` is set manually for deal sync
- `stages` — belongs to a pipeline; `position`, `forecast_type`, `probability`
- `owners` — sales reps / deal owners

**Current state** (overwritten on each sync — "what is true now"):
- `deals_snapshot` — one row per deal: amount, stage, owner, age/rotten days,
  curated `cf_*` custom fields, plus full `custom_fields` and `raw_payload` JSONB
- `tasks_snapshot`, `email_activity` — activity mirrors (second-pass)

**History** (append-only — "what changed over time"):
- `deal_events` — `created` / `stage_change` / `owner_change` with old→new ids,
  `occurred_at`, and `source` (`webhook` | `timeline_backfill`)
- `pipeline_daily_snapshot` — per-day stage rollups for trend charts (second-pass)

**Auth:**
- `dashboard_users` — email PK, `role` (`superadmin`/`gmd`/`sales_manager`/`rep`),
  optional `owner_id` link, bcrypt `hashed_password`

The split between **snapshot** (current) and **events** (history) is deliberate:
snapshots answer "what does the funnel look like now," events answer "how did
deals move," without needing to poll the CRM for history.

---

## Data flow

**Reference sync** (`services/reference_sync.py`) — startup (optional) + daily job.
Pulls pipelines (with embedded stages) and owners, upserts reference tables,
excludes the Test pipeline, derives `business_line`, and builds a
`PipelineStageResolver` (name→id lookup) cached on `app.state` for webhook use.

**Deal sync** (`services/deal_sync.py`) — every 20 min. For each active pipeline's
`view_id`, paginates `/crm/sales/api/deals/view/{id}`, upserts `deals_snapshot`, refreshes
`age_days`/`rotten_days`. Guarded by a Postgres advisory lock so overlapping or
multi-instance runs don't collide.

**Webhook ingest** (`services/webhook_ingest.py`) — real-time. Freshsales workflow
automation POSTs to `/webhooks/freshsales/deal`; the handler verifies the HMAC
secret, resolves stage/owner names to ids, diffs against the stored snapshot
(`change_detection.py`), appends a `deal_events` row, and upserts the snapshot.
Returns `503` until the first reference sync has populated the resolver.

---

## API surface

All under `/api/v1` unless noted. Auth uses JWT in an httpOnly cookie (browsers)
**and** in the response body (API clients). Interactive docs at `/docs`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/login` | public | email/password → access + refresh tokens |
| GET | `/auth/me` | cookie/bearer | current user |
| POST | `/auth/refresh` | refresh cookie | rotate tokens |
| POST | `/auth/logout` | — | clear cookies |
| POST | `/admin/sync/reference` | gmd, superadmin | trigger reference sync |
| POST | `/admin/sync/deals` | gmd, superadmin | trigger deal sync |
| POST | `/admin/users` | superadmin | create a dashboard user |
| GET | `/admin/users` | superadmin | list users |
| POST | `/webhooks/freshsales/deal` | HMAC header | ingest a deal webhook *(no `/api/v1`)* |
| GET | `/healthz` · `/readyz` | public | liveness · readiness (db ping) |

Responses use a consistent envelope (`status`, `status_code`, plus typed payload)
defined in `app/schemas/responses.py`.

---

## Local development

Requires [`uv`](https://docs.astral.sh/uv/) and a Postgres (local or Supabase).

```bash
cp .env.example .env          # then fill in DATABASE_URL, FRESHSALES_*, JWT_SECRET
uv sync --all-extras          # install deps (incl. dev)
uv run alembic upgrade head   # apply migrations
uv run python scripts/seed_user.py you@churchgate.com superadmin   # first user
uv run fastapi dev app/main.py   # http://localhost:8000/docs
```

`scripts/run.sh` does the sync + migrate + run steps in one shot.

> In dev, set `SYNC_ON_STARTUP=false` so `fastapi dev` auto-reloads don't call
> Freshsales on every file save; trigger reference data manually via
> `POST /api/v1/admin/sync/reference`.

### Common commands

```bash
uv run pytest -q                       # run tests
uv run ruff check app tests            # lint
uv run alembic revision --autogenerate -m "msg"   # new migration
```

---

## Configuration

All settings come from environment variables (see `.env.example` for the full
list and inline docs). Key ones:

| Var | Notes |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…`. Use Supabase **direct** connection (port 5432) for advisory locks |
| `FRESHSALES_DOMAIN` / `FRESHSALES_API_KEY` | CRM subdomain + API key |
| `FRESHSALES_WEBHOOK_SECRET` | must match the secret configured on the Freshsales webhook |
| `JWT_SECRET` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `RUN_SCHEDULER` | enable APScheduler jobs |
| `SYNC_ON_STARTUP` | run reference sync on boot (true in prod, false in dev) |
| `ENVIRONMENT` | `production` enables secure cookies + JSON logs |

---

## Deployment (Railway)

`railway.toml` builds the Dockerfile and runs
`alembic upgrade head && fastapi run app/main.py`, with `/healthz` as the
healthcheck. Set `DATABASE_URL` (Supabase direct, port 5432), `JWT_SECRET`,
`FRESHSALES_API_KEY`, `FRESHSALES_WEBHOOK_SECRET`, and `ENVIRONMENT=production`.

---

## Branching

`feature/*` → `development` → `staging` → `main`. All branches are protected and
require the `test` CI check to pass; `staging` and `main` additionally require a
PR review. See `.github/pull_request_template.md`.
