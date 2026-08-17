"""How the application talks to the Coastwise Registry fixture.

One request to the provider carries a whole batch of lookups, which is how a metered provider is
normally used and is also what makes the accounting legible: the provider charges for the lookups it
was asked to perform, in one place, and reports them on its own bill.

The client itself imposes no limit on how many lookups it will ask for. That is deliberate — a
client library is the wrong place for this project's bound, and pretending otherwise would hide the
control the demonstration is about. The limit belongs in the application, before the work is
admitted, which is where the secure variant puts it.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

import httpx

from .config import require_allowed_target
from .models import LookupResponse


class ProviderClient:
    """Speaks to the in-network provider fixture, and to nothing else."""

    def __init__(self, base_url: str, *, timeout: float = 30.0, max_connections: int = 64) -> None:
        # The destination is validated here as well as in configuration, so no code path can reach
        # a host that is not one of this demonstration's own services.
        self._base_url = require_allowed_target(base_url)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def lookups(self, *, tenant_id: str, company_names: list[str]) -> LookupResponse:
        """Perform the named lookups at the provider and return what it charged for them."""
        response = await self._client.post(
            f"{self._base_url}/v1/lookups",
            json={
                "tenant_id": tenant_id,
                "items": [{"company_name": name} for name in company_names],
            },
        )
        response.raise_for_status()
        return LookupResponse.model_validate(response.json())

    async def ledger(self) -> dict[str, object]:
        response = await self._client.get(f"{self._base_url}/ledger")
        response.raise_for_status()
        payload: dict[str, object] = response.json()
        return payload
