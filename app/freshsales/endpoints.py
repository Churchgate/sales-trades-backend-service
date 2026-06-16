"""Explicit Freshsales Classic API endpoint registry (spec §5).

This account uses the Freshsales Classic API (base: /api/), not the newer
Freshsales Suite prefix (/crm/sales/api/). Each path is spelled out explicitly.

Note: the Classic API embeds deal_stages inside each pipeline object returned by
deal_pipelines() — there is no separate per-pipeline stages endpoint.
"""


def deals_view(view_id: int, page: int = 1) -> str:
    """Deals list for a pipeline view. Paginated, 25/page default."""
    return f"/api/deals/view/{view_id}?page={page}"


def deal_detail(deal_id: int) -> str:
    """Full deal record including custom_field block."""
    return f"/api/deals/{deal_id}"


def deal_pipelines() -> str:
    """All deal pipelines with embedded deal_stages[]. Cache — rarely changes."""
    return "/api/selector/deal_pipelines"


def owners() -> str:
    """All owners/users. Cache, refresh daily."""
    return "/api/selector/owners"


def deal_timeline_feeds(deal_id: int, page: int = 1) -> str:
    """Deal timeline (stage/owner changes, tasks). Paginate via meta.has_next."""
    return f"/api/deals/{deal_id}/timeline_feeds?page={page}"


def deal_notes(deal_id: int) -> str:
    """Deal notes."""
    return f"/api/deals/{deal_id}/notes"


def deal_tasks(deal_id: int) -> str:
    """Deal tasks."""
    return f"/api/deals/{deal_id}/tasks"


def deal_conversations(deal_id: int) -> str:
    """Email conversations."""
    return f"/api/deals/{deal_id}/conversations/all"
