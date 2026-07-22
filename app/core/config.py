from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/churchgate_dashboard"
    )
    test_database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/churchgate_dashboard_test"
    )
    database_pool_size: int = 10
    database_max_overflow: int = 5
    database_pool_timeout: int = 30

    # --- Freshsales ---
    freshsales_domain: str = "rbpropertieslimited"
    freshsales_api_key: str = ""
    freshsales_tz: str = "Africa/Lagos"
    freshsales_webhook_secret: str = ""
    freshsales_rate_limit_per_hour: int = 1000
    # Global deal smart-view ids synced into deals_snapshot. Open + Won + Lost
    # together cover every non-deleted deal exactly once (status-partitioned);
    # upsert dedups by deal_id so any overlap is harmless. Comma-separated.
    freshsales_deal_view_ids: str = "17001462746,17001462752,17001462751"

    # --- Google Analytics (GA4 Data API) ---
    # Numeric GA4 property id (Admin → Property settings), not the G-XXXX tag id.
    ga_property_id: str = ""
    # The service-account key JSON, as a raw string (secret). Empty disables the
    # website-analytics panel (endpoint returns configured=false).
    ga_service_account_json: str = ""

    # --- Frontend ---
    frontend_base_url: str = "http://localhost:3000"

    # --- Bookings / Email (SendGrid) ---
    # Standalone booking frontend origin (CORS). The booking API is public, but the
    # browser still needs this origin allow-listed for credentialed/fetch requests.
    booking_frontend_base_url: str = "http://localhost:3001"
    booking_tz: str = "Africa/Lagos"
    sendgrid_api_key: str = ""
    mail_from_email: str = "no-reply@example.com"
    mail_from_name: str = "WTC Abuja Bookings"

    # --- Campaign emails (SendGrid — WTC Abuja account) ---
    # Digital-pack delivery and lead-capture notifications use a separate SendGrid
    # account (enquiries@wtcabuja.com) rather than the shared Churchgate no-reply.
    # Set WTC_SENDGRID_API_KEY to enable; leave empty to skip sends in dev/QA.
    wtc_sendgrid_api_key: str = ""
    # Recipient for the internal "New Lead" notification sent to the sales team.
    # Leave empty to disable notifications (e.g. dev/QA environments).
    campaign_notification_email: str = ""
    # CC'd on every digital-pack email sent to a visitor, so the team gets a copy
    # of exactly what was delivered (audit/verification). Leave empty to disable.
    campaign_cc_email: str = ""
    # Base64 ECDSA public key from SendGrid > Settings > Mail Settings > Event
    # Webhook > Signed Event Webhook. Verifies POSTs to /webhooks/sendgrid/events
    # actually came from SendGrid. Leave empty to accept unsigned (e.g. before the
    # signing key is configured in SendGrid, or in dev) — logs a warning either way.
    sendgrid_webhook_public_key: str = ""

    # --- Booth/stand lead capture ---
    # Management dashboard origin (CORS), if deployed separately from the API.
    dashboard_frontend_base_url: str = "http://localhost:3002"
    # Kiosk/QR booth app origin (CORS) — the public-facing tablet + QR-to-phone
    # form that POSTs to /campaigns/{slug}/leads directly (no proxy route by
    # design). Distinct from dashboard_frontend_base_url, which is the
    # admin-only campaign/leads management UI.
    kiosk_frontend_base_url: str = "http://localhost:3003"
    # Any further CORS origins beyond the fixed ones above (e.g. the public
    # wtcabuja.com marketing site embedding the kiosk form) — comma-separated,
    # no redeploy-worthy code change needed to add one. Empty by default.
    extra_cors_origins: str = ""
    # Live Freshsales contact write-sync. Off by default so an event can run
    # CSV-first (the guaranteed path) and flip this on once verified live.
    freshsales_lead_sync_enabled: bool = False
    lead_crm_sync_interval_minutes: int = 10
    # Pull-back sync: advances triage_status when Freshsales' own last_contacted
    # shows a rep already worked the lead directly in the CRM. Per-lead GET fan-out
    # like nog_activity_sync, but scoped to still-`new` leads only, so the working
    # set shrinks over time rather than re-scanning every synced lead.
    triage_sync_interval_minutes: int = 30
    # NOG per-contact activity sync (calls/emails/meetings/notes for the NOG
    # Activities page). Heavy per-contact fan-out over ~430 contacts, so it runs
    # once nightly. Off by default — flip on once the contacts are assigned/synced.
    nog_activity_sync_enabled: bool = False
    # Backstop sweep for digital-pack emails not delivered inline at capture
    # (e.g. captured offline, or a transient send failure). Capture also attempts
    # delivery inline so most packs go out immediately; this just retries the rest.
    pack_delivery_interval_minutes: int = 5
    # Digital-pack emails can send from their own sender identity, distinct from
    # bookings' no-reply@churchgate.com (a separate application's address) — but
    # only once that address is a verified Sender Identity (or part of an
    # authenticated domain) in SendGrid, or sends fail outright (403). Confirmed
    # 2026-06-30: events@wtcabuja.com is NOT yet verified — empty by default so
    # event emails fall back to the shared (already-verified) no-reply sender
    # until this is set. Set both env vars once verified; no redeploy needed.
    event_mail_from_email: str = ""
    event_mail_from_name: str = ""

    # --- JWT ---
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_minutes: int = 60 * 24 * 14

    # --- Scheduler ---
    run_scheduler: bool = True
    reference_sync_interval_hours: int = 24
    # Task + email activity syncs are per-deal (rate-limit sensitive, spec §7).
    activity_sync_interval_minutes: int = 60
    # Run the reference sync on app startup. Disable in dev so `fastapi dev`
    # auto-reloads don't hit Freshsales on every file save; trigger it manually
    # via POST /api/v1/admin/sync/reference instead.
    sync_on_startup: bool = True

    # --- Logging agent (WhatsApp/Telegram → CRM) ---
    # Shared secret the n8n workflow presents (header) to POST /webhooks/agent/log.
    agent_webhook_secret: str = ""
    # Pipelines the logging agent may write to. Starts locked to the Test pipeline
    # so testing can never touch real deals; widen (comma-separated) once verified.
    agent_allowed_pipeline_ids: str = "17000075034"

    # --- Lead intelligence (Apollo enrichment + OpenRouter ICP scoring) ---
    # Both leave empty to disable in dev/QA; scripts/enrich_leads.py and
    # scripts/score_leads_icp.py degrade to dry-run-only without them.
    apollo_api_key: str = ""
    openrouter_api_key: str = ""

    @property
    def freshsales_base_url(self) -> str:
        # Freshsales Suite host. The endpoint paths in app/freshsales/endpoints.py
        # carry the /crm/sales/api prefix, so base_url is the bare host.
        return f"https://{self.freshsales_domain}.myfreshworks.com"

    @property
    def deal_view_ids(self) -> list[int]:
        return [int(v) for v in self.freshsales_deal_view_ids.split(",") if v.strip()]

    @property
    def cors_origins(self) -> list[str]:
        fixed = [
            self.frontend_base_url,
            self.booking_frontend_base_url,
            self.dashboard_frontend_base_url,
            self.kiosk_frontend_base_url,
        ]
        extra = [v.strip() for v in self.extra_cors_origins.split(",") if v.strip()]
        return fixed + extra

    @property
    def agent_allowed_pipelines(self) -> set[int]:
        return {int(v) for v in self.agent_allowed_pipeline_ids.split(",") if v.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
