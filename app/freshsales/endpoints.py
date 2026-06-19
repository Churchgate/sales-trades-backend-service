"""Explicit Freshsales Suite API endpoint registry (spec §5).

This account is Freshsales **Suite**, served from `{domain}.myfreshworks.com`
under the `/crm/sales/api` prefix (NOT the legacy `{domain}.freshsales.io/api`
host, which is a generic web host that rate-limits all traffic). The base host
is set in `Settings.freshsales_base_url`; the paths below carry the prefix.

Note: the `/selector/deal_pipelines` response embeds `deal_stages[]` inside each
pipeline object, so there is no need to call the separate stages endpoint.
"""


def deals_view(view_id: int, page: int = 1) -> str:
    """Deals list for a saved view. `include=owner,deal_reason` sideloads owner_id and
    deal_reason_id (both absent from bare view records; verified live). Paginated,
    25/page default."""
    return f"/crm/sales/api/deals/view/{view_id}?include=owner,deal_reason&page={page}"


def deal_detail(deal_id: int) -> str:
    """Full deal record (wrapped in `deal`) incl. custom_field; `include=owner,deal_reason`
    adds owner_id and the lost/won deal_reason_id (verified live)."""
    return f"/crm/sales/api/deals/{deal_id}?include=owner,deal_reason"


def filtered_search_deal(page: int = 1) -> str:
    """POST a filter_rule to search deals across pipelines. Records are thin
    (no pipeline/stage/owner), so use it only to enumerate deal ids."""
    return f"/crm/sales/api/filtered_search/deal?page={page}"


def deal_pipelines() -> str:
    """All deal pipelines with embedded deal_stages[]. Cache — rarely changes."""
    return "/crm/sales/api/selector/deal_pipelines"


def owners() -> str:
    """All owners/users (response top-level key is `users`). Cache, refresh daily."""
    return "/crm/sales/api/selector/owners"


def deal_reasons() -> str:
    """Lost/won deal-reason lookup (id -> name) under key `deal_reasons`. Cache,
    refresh daily."""
    return "/crm/sales/api/selector/deal_reasons"


def deal_timeline_feeds(deal_id: int, page: int = 1) -> str:
    """Deal timeline (stage/owner changes, tasks). Paginate via meta.has_next."""
    return f"/crm/sales/api/deals/{deal_id}/timeline_feeds?page={page}"


def deal_notes(deal_id: int) -> str:
    """Deal notes."""
    return f"/crm/sales/api/deals/{deal_id}/notes"


def deal_tasks(deal_id: int) -> str:
    """Deal tasks. Unlike most Suite endpoints this is served WITHOUT the `/api`
    segment (spec §5) — the `/crm/sales/api/.../tasks` form 404s (verified live).
    Response wraps the list under `tasks`."""
    return f"/crm/sales/deals/{deal_id}/tasks"


def deal_conversations(deal_id: int) -> str:
    """Email conversations. Also served WITHOUT `/api` (spec §5; `/api/...` 404s,
    verified live). Response wraps the list under `email_conversations`."""
    return f"/crm/sales/deals/{deal_id}/conversations/all"
