# Backend Implementation Plan — Analytics & Activity Layer

> Source: data-exploration audit of the Freshsales CRM (v1 dashboard review).
> The weekly question this backend must answer:
> **"What is alive, who owns it, what is the next action, and what is likely to close?"**

## Where we are today

The backend is **ingestion-only**:

- ✅ Reference sync (pipelines, stages, owners) — `app/services/reference_sync.py`
- ✅ Deal snapshot sync from views + non-default pipelines — `app/services/deal_sync.py`
- ✅ Webhook ingest + change detection (`deal_events`) — `app/services/webhook_ingest.py`, `change_detection.py`
- ✅ Auth (JWT cookies, roles `superadmin`/`gmd`/`sales_manager`/`rep`) — `app/api/v1/endpoints/auth.py`
- ✅ `deals_enriched` view (migration `ad205b8f1f36`) exposing `business_line`, `stage_name`,
  `pipeline_name`, `forecast_type`, `stage_probability`, `stage_position` alongside all `deals_snapshot` columns.

**Gaps that block the audit's asks:**

- ❌ No analytics/query layer at all (router only mounts `auth`, `admin`).
- ❌ `tasks_snapshot` and `email_activity` tables exist but have **no sync logic** → "next action" and
  "last activity" cannot be computed.
- ❌ `pipeline_daily_snapshot` table is **never written** → no trend/week-over-week comparison.
- ❌ No deal/reference read endpoints.

## What the audit asked for (the lens)

**Preserve:** business-line separation, pipeline-health headline, stale-value analysis, action roadmap,
DB-integrity findings.

**Add:** (1) stale clarity — last stage move AND last activity; (2) owner accountability; (3) true active
pipeline (exclude dormant/duplicate/dead); (4) next-action/follow-up discipline; (5) lead-source performance;
(6) prominent leads-module gap; (7) loss reasons by category; (8) ageing by stage AND owner; (9) "data as of"
on every view.

**Tone down (server-side flags so the UI can):** blended totals → `is_blended:true`; loss reasons → expose
`% with no reason recorded`; staleness → echo the definition + denominator; contact reachability → expose
"unreachable by phone" specifically.

## Carried over from the original plan (spec §9) — nothing dropped

The original build roadmap is spec §9, steps 1–8. Status against this plan:

| # | Spec §9 step | Status |
|---|---|---|
| 1 | Scaffold (endpoints, schema, webhook, sync job) | ✅ done |
| 2 | Reference-table sync (startup + daily) | ✅ done |
| 3 | Webhook handler + change detection → `deal_events` | ✅ done |
| 4 | Scheduled deal-view sync per pipeline | ✅ done |
| 5 | **Timeline backfill one-off script (§6C)** | ⏳ **Phase B1b below** |
| 6 | **Email/task sync for response-time & follow-up SLA (§6D/§6E)** | ⏳ **Phase B1 below** |
| 7 | Aggregation endpoints (GMD overview, drill-down, rep view) | ⏳ **Phase B2 below** |
| 8 | Next.js dashboard + role mapping | → `frontend/PLAN.md` |

Plus the audit additions (active pipeline, owner accountability, staleness clarity, loss reasons, lead source,
data-quality/leads-gap, trends) layered on top.

---

## Phase B0 — Analytics plumbing

- Create `app/api/v1/endpoints/analytics.py` and mount it in `app/api/v1/router.py`
  (currently `api_router.include_router(auth.router)` / `admin.router` only).
- Create `app/repositories/analytics_repo.py` — all aggregation SQL lives here, reading the
  **`deals_enriched`** view (not raw `deals_snapshot`) so `business_line`/`stage_*`/`pipeline_name` come free.
- Create `app/schemas/analytics.py` — query-param models: `business_line`, `pipeline_id`, `owner_id`,
  `stale_days` (default 30), `as_of`.
- Extend `app/schemas/responses.py` with typed DTOs (subclass `BaseResponse`: `status`, `status_code`).
- Role-gate with the existing dependency pattern. `gmd`/`sales_manager`/`superadmin` see everything;
  `rep` is **scoped to its own `owner_id`** (from `dashboard_users.owner_id`).
- **Every response carries `data_as_of`** = `max(last_synced_at)` over `deals_snapshot`.

## Phase B1 — Activity syncs (unblocks "next action" & "last activity")

- **Tasks sync** — new `app/services/task_sync.py` + job in `app/jobs/tasks.py`. Pull
  `/crm/sales/deals/{id}/tasks` (builder already in `app/freshsales/endpoints.py`) into `tasks_snapshot`,
  scoped to open deals. New advisory-lock key `17000003` in `app/core/scheduler.py`. (Spec §6E)
- **Email-activity sync** — new `app/services/email_sync.py` + job. Pull
  `/crm/sales/deals/{id}/conversations/all` into `email_activity`. Advisory key `17000004`. (Spec §6D)
  - Enables the **first-response-time** metric: first `direction='outgoing'` `conversation_time` minus
    `deal_created_at` (the column added in migration `ad205b8f1f36`) — "time to first outreach".
- **Tasks → follow-up SLA:** tasks sync also powers the **"overdue follow-up tasks per rep"** alert
  (Vinay's #1 priority, §6E), surfaced via `/analytics/owners` and `/analytics/next-actions`.
- **`last_activity_at`** is computed in SQL (not stored, to start) as
  `max(stage_updated_at, latest task due/completed, latest email conversation_time, latest deal_event.occurred_at)`.
- Reuse the existing rate-limited `app/freshsales/client.py`; reuse parsing helpers in `app/freshsales/parsing.py`.
- **Rate-limit caution (§7):** 1000 req/hr/account. Scope per-deal task/email pulls to *open* deals and
  prefer the scheduled cadence; let webhooks carry real-time load.

## Phase B1b — Timeline backfill (spec §9 #5 / §6C) — seeds trend history

- New one-off + periodic script `app/services/timeline_backfill.py` (runnable via an admin endpoint
  and/or a low-frequency job). For deals lacking `deal_events` history, pull
  `/crm/sales/api/deals/{id}/timeline_feeds` (builder already in `app/freshsales/endpoints.py`).
- Paginate by incrementing `page` until `meta.has_next == false` (no `total`/`total_pages` — §7).
- Extract `STAGE_CHANGE` / `OWNER_CHANGE` events (these carry `stage_id` + `pipeline_name` in
  `action_data`, so resolution is simpler than webhooks). Insert as `deal_events` rows with
  `source='timeline_backfill'`.
- **Quirk (§7):** do **not** filter tasks on exact `action_type` (≈91% are corrupted by the "Deal
  Followup Remainder" workflow) — match `actionable_type == "Task"` instead.
- Why it matters: seeds `deal_events` (and therefore `/analytics/trends`) with real history on day one
  instead of starting blank.

## Phase B2 — Core analytics endpoints

The five roadmap routes — `overview`, `pipeline`, `revenue`, `owners` (the roadmap "reps" route), and
`staleness` — are all included below; `owners` carries the audit's accountability columns, and the remaining
rows (active-pipeline, next-actions, loss-reasons, ageing, lead-source, data-freshness) are the audit additions.

All accept `business_line` / `pipeline_id` / `owner_id` filters and return the standard envelope + `data_as_of`.

| Endpoint | Answers | Notes |
|---|---|---|
| `GET /analytics/overview` | per-business-line health | win rate, open value, active vs total; blended totals present but flagged `is_blended:true` (de-emphasise client-side) |
| `GET /analytics/pipeline` | funnel: count + value by stage | ordered by `stage_position`; per business line |
| `GET /analytics/revenue` | value by close month — **"what is likely to close"** | won value + weighted-open value bucketed by `expected_close_date` month, weighted by `stage_probability`; split by `forecast_type` (committed / best-case / pipeline); per business line |
| `GET /analytics/owners` *(roadmap "reps" route)* | per-owner performance + accountability | win rate, open/won value **and** the audit's accountability columns: stale value, #no-next-action, #no-follow-up-date, #no-activity-in-N-days, last-CRM-update, **plus #deals-progressed** (stage advances in last N days, from `deal_events`) so "who is *actually progressing* deals" is answered, not just who is stalled. Replaces the basic roadmap "reps" route by folding accountability in |
| `GET /analytics/active-pipeline` | **true live pipeline** | excludes dormant (last-activity/`age_days` > 730d), `cf_deal_status` dead/duplicate, lost; returns live value **and** the excluded set with reasons |
| `GET /analytics/staleness` | stale by stage AND owner | returns BOTH `days_since_stage_move` and `days_since_activity`; `stale_days` param (default 30); echoes definition + denominator |
| `GET /analytics/next-actions` | follow-up discipline | % open deals with a next task, with follow-up/expected-close date, with recent activity |
| `GET /analytics/loss-reasons` | loss reasons by category | maps raw `lost_reason`/`lost_reason_id` onto the audit's categories (pricing, poor follow-up, wrong target, no budget, competitor, timing, broker issue, product mismatch) via a static lookup in `analytics_repo`; surfaces **% lost with no reason recorded** and an "uncategorised" bucket for unmapped reasons |
| `GET /analytics/ageing` | age by stage & owner | buckets 0-30 / 30-90 / 90-365 / 365+; drillable to owner (e.g. who owns "Offer Letter Sent") |
| `GET /analytics/lead-source` | channel performance | broker/direct/referral/event/website; if source field is unmapped, return `available:false` + reason rather than guessing |
| `GET /analytics/data-freshness` | per-sync last-run | last reference/deal/task/email sync timestamps |

## Phase B3 — Trend history

- Write the **`pipeline_daily_snapshot`** job (table exists, never populated): nightly roll-up of
  `deal_count` + `total_value` + `total_base_currency_value` by `(snapshot_date, pipeline_id, stage_id)`.
- `GET /analytics/trends` — week-over-week pipeline movement, enabling the "compare future versions"
  the audit asked for.

## Phase B4 — Data-integrity & leads-gap endpoints

- `GET /analytics/data-quality` — deals missing owner/stage/source; (once contacts are ingested)
  **% unreachable by phone**, duplicates, orphaned contacts. Backs the DB-integrity section.
- `GET /analytics/leads-status` — explicit **"leads module not yet ingested"** flag so the frontend can
  render a prominent top-of-funnel warning instead of silently omitting it.

---

## Deliverables

**New files**
- `app/api/v1/endpoints/analytics.py`
- `app/repositories/analytics_repo.py`
- `app/schemas/analytics.py`
- `app/services/task_sync.py`
- `app/services/email_sync.py`
- `app/services/timeline_backfill.py` (one-off + periodic; admin-triggerable)

**Edited files**
- `app/api/v1/router.py` — mount analytics router
- `app/schemas/responses.py` — analytics DTOs
- `app/api/v1/endpoints/admin.py` — add a `POST /admin/backfill/timeline` trigger (alongside existing sync triggers)
- `app/jobs/tasks.py` — task-sync + email-sync + daily-snapshot jobs
- `app/core/scheduler.py` — register jobs + advisory keys `17000003`/`17000004`/`17000005` (and `17000006` if backfill runs periodically)

**Reuse (do not rebuild)**
- `deals_enriched` view (`ad205b8f1f36`)
- `app/freshsales/client.py`, `endpoints.py` (task/conversation builders already defined), `parsing.py`
- `app/freshsales/parsing.py::PipelineStageResolver`
- Existing role dependency from `app/api/dependencies.py`

**Migrations:** none required — analytics is read-only over existing tables; `tasks_snapshot` and
`email_activity` already exist.

---

## Verification

- `uv run pytest` — add tests for each aggregation against a seeded `deals_enriched`.
- `/docs` — exercise each `/analytics/*` endpoint; confirm `data_as_of` is present, staleness returns
  both day-counts and echoes its definition, and `rep` role is scoped to own deals.
- Confirm task/email jobs populate `tasks_snapshot` / `email_activity` and that `last_activity_at` reflects them.
- Run the timeline backfill against a sample of deals; confirm `deal_events` rows land with
  `source='timeline_backfill'` and that `/analytics/trends` shows history.
- Confirm `pipeline_daily_snapshot` accrues rows nightly and `/analytics/trends` returns deltas.
- Cross-check headline numbers (true active pipeline; stale value at "Offer Letter Sent") against the audit
  figures to validate the queries.
- CI: the existing `feature/* → development → staging → main` flow with the required `test` check still
  applies — every phase ships behind a passing `uv run pytest`.

## Suggested build order

B0 → B1 → B1b → B2 (overview, pipeline, staleness, owners, active-pipeline first; loss-reasons, ageing,
next-actions, lead-source next) → B4 → B3.
