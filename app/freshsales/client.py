import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.freshsales import endpoints

logger = get_logger(__name__)


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


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


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
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def get(self, path: str) -> dict[str, Any]:
        await self._rate_limiter.acquire()
        response = await self._client.get(path)
        response.raise_for_status()
        return response.json()

    # --- Reference data ---

    async def get_pipelines(self) -> dict[str, Any]:
        return await self.get(endpoints.deal_pipelines())

    async def get_owners(self) -> dict[str, Any]:
        return await self.get(endpoints.owners())

    # --- Deals ---

    async def get_deal(self, deal_id: int) -> dict[str, Any]:
        return await self.get(endpoints.deal_detail(deal_id))

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

    # --- Timeline (for backfill) ---

    async def paginate_timeline(self, deal_id: int) -> AsyncIterator[dict[str, Any]]:
        """Yield timeline feed entries, paginating via meta.has_next (no total count)."""
        page = 1
        while True:
            data = await self.get(endpoints.deal_timeline_feeds(deal_id, page=page))
            for feed in data.get("timeline", []):
                yield feed
            if not data.get("meta", {}).get("has_next"):
                break
            page += 1
