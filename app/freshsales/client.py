import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from tenacity import RetryCallState, retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.freshsales import endpoints

logger = get_logger(__name__)

# Never block a request (or hammer the gateway) honouring a Retry-After longer
# than this. Freshsales' edge gateway (Istio/Envoy) IP-rate-limits *before* auth
# and returns multi-minute Retry-After values; retrying those just keeps the
# penalty window warm, so we fail fast and let the scheduler try again later.
_MAX_RETRY_AFTER_SECONDS = 30.0


class RateLimiter:
    """Token-bucket limiter keeping requests well under Freshsales' rate cap."""

    def __init__(self, max_per_hour: int) -> None:
        self._capacity = float(max_per_hour)
        self._tokens = float(max_per_hour)
        self._refill_rate = max_per_hour / 3600.0  # tokens per second
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
                self._updated_at = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_time = (1 - self._tokens) / self._refill_rate
                await asyncio.sleep(wait_time)


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Parse the Retry-After header (delta-seconds form) from a 429/503, if present."""
    if isinstance(exc, httpx.HTTPStatusError):
        raw = exc.response.headers.get("retry-after")
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            # Only retry short, app-level throttles. A long Retry-After means the
            # edge gateway has IP-banned us for minutes — retrying is futile and
            # keeps the penalty window warm, so fail fast instead.
            retry_after = _retry_after_seconds(exc)
            return retry_after is None or retry_after <= _MAX_RETRY_AFTER_SECONDS
        return code >= 500
    return isinstance(exc, httpx.TransportError)


def _wait_strategy(retry_state: RetryCallState) -> float:
    """Honour Retry-After when the server sends it; else exponential backoff."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc is not None:
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None:
            return min(retry_after, _MAX_RETRY_AFTER_SECONDS)
    return wait_exponential(multiplier=1, min=1, max=30)(retry_state)


class FreshsalesClient:
    """Async HTTPX client for the Freshsales REST API.

    Applies `Authorization: Token token=<API_KEY>`, a token-bucket rate limiter
    (spec §5: 1000 req/hr/account), and retries with backoff on 429/5xx.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self._rate_limiter = RateLimiter(settings.freshsales_rate_limit_per_hour)
        self._client = httpx.AsyncClient(
            base_url=settings.freshsales_base_url,
            headers={"Authorization": f"Token token={settings.freshsales_api_key}"},
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "FreshsalesClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        wait=_wait_strategy,
        reraise=True,
    )
    async def get(self, path: str) -> dict[str, Any]:
        await self._rate_limiter.acquire()
        response = await self._client.get(path)
        response.raise_for_status()
        return response.json()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        wait=_wait_strategy,
        reraise=True,
    )
    async def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        await self._rate_limiter.acquire()
        response = await self._client.post(path, json=json)
        response.raise_for_status()
        return response.json()

    # --- Reference data ---

    async def get_pipelines(self) -> dict[str, Any]:
        return await self.get(endpoints.deal_pipelines())

    async def get_owners(self) -> dict[str, Any]:
        return await self.get(endpoints.owners())

    async def get_deal_reasons(self) -> dict[str, Any]:
        return await self.get(endpoints.deal_reasons())

    # --- Deals ---

    async def get_deal(self, deal_id: int) -> dict[str, Any]:
        """Full deal record (the unwrapped `deal` object)."""
        data = await self.get(endpoints.deal_detail(deal_id))
        return data.get("deal", data)

    async def paginate_view(self, view_id: int) -> AsyncIterator[dict[str, Any]]:
        """Yield each deal record from a deals/view endpoint, paginating until empty."""
        page = 1
        while True:
            data = await self.get(endpoints.deals_view(view_id, page=page))
            deals = data.get("deals", [])
            if not deals:
                break
            for deal in deals:
                yield deal
            page += 1

    async def iter_pipeline_deal_ids(self, pipeline_id: int) -> AsyncIterator[int]:
        """Yield deal ids for one pipeline via filtered_search (reaches non-default
        pipelines that the system views can't). Records are thin, so we only take ids."""
        rule = {
            "filter_rule": [
                {"attribute": "deal_pipeline_id", "operator": "is_in", "value": [pipeline_id]}
            ]
        }
        page = 1
        seen = 0
        while True:
            body = await self.post(endpoints.filtered_search_deal(page=page), rule)
            deals = body.get("deals", [])
            if not deals:
                break
            for deal in deals:
                yield deal["id"]
            seen += len(deals)
            total = body.get("meta", {}).get("total")
            if total is not None and seen >= total:
                break
            page += 1

    # --- Activity (tasks + email conversations) ---

    async def get_deal_tasks(self, deal_id: int) -> dict[str, Any]:
        """Tasks for one deal (spec §6E). Response wraps the list under `tasks`."""
        return await self.get(endpoints.deal_tasks(deal_id))

    async def get_deal_conversations(self, deal_id: int) -> dict[str, Any]:
        """Email conversations for one deal (spec §6D). Response wraps the list
        under `email_conversations`."""
        return await self.get(endpoints.deal_conversations(deal_id))

    # --- Timeline (for backfill) ---

    async def paginate_timeline(self, deal_id: int) -> AsyncIterator[dict[str, Any]]:
        """Yield timeline feed entries, paginating via meta.has_next (no total count).

        The response wraps the list under `timeline_feeds` (verified live)."""
        page = 1
        while True:
            data = await self.get(endpoints.deal_timeline_feeds(deal_id, page=page))
            for feed in data.get("timeline_feeds", []):
                yield feed
            if not data.get("meta", {}).get("has_next"):
                break
            page += 1
