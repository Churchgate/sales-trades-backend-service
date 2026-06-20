from pydantic import BaseModel, ConfigDict


class FreshsalesDealWebhook(BaseModel):
    """Lenient schema over the flat `deal_*` webhook payload (spec §6A).

    Freshsales workflow automation POSTs the deal's *current full state* (not a
    diff) using flat `deal_*` keys, pipeline/stage as **names** (not IDs), and
    `MM-DD-YYYY [HH:MM:SS]` timestamps with no explicit timezone.

    Only the fields needed for ingestion are declared; the ~37 `deal_cf_*` custom
    fields, `deal_sales_account_*` fields, etc. are preserved via `extra="allow"`
    and the caller works from the raw payload dict for those.
    """

    model_config = ConfigDict(extra="allow")

    deal_id: int
    deal_name: str | None = None
    deal_amount: float | None = None
    deal_base_currency_amount: float | None = None
    deal_owner_id: int | None = None
    deal_deal_pipeline_name: str | None = None
    deal_deal_stage_name: str | None = None
    deal_stage_updated_time: str | None = None
    deal_created_at: str | None = None
    deal_expected_close: str | None = None
    deal_lost_reason: str | None = None
    deal_lost_reason_id: int | None = None
    deal_sales_account_id: int | None = None
    deal_sales_account_name: str | None = None
