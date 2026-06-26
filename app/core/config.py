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

    # --- Booth/stand lead capture ---
    # Management dashboard origin (CORS), if deployed separately from the API.
    dashboard_frontend_base_url: str = "http://localhost:3002"
    # Live Freshsales contact write-sync. Off by default so an event can run
    # CSV-first (the guaranteed path) and flip this on once verified live.
    freshsales_lead_sync_enabled: bool = False
    lead_crm_sync_interval_minutes: int = 10

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

    @property
    def freshsales_base_url(self) -> str:
        # Freshsales Suite host. The endpoint paths in app/freshsales/endpoints.py
        # carry the /crm/sales/api prefix, so base_url is the bare host.
        return f"https://{self.freshsales_domain}.myfreshworks.com"

    @property
    def deal_view_ids(self) -> list[int]:
        return [int(v) for v in self.freshsales_deal_view_ids.split(",") if v.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
