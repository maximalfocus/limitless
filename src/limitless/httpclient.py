"""The shared HTTP boundary: one client, no scheduling policy.

This module knows how to speak to a replica and how to record what happened. It deliberately does
*not* decide when requests are sent — that is the caller's business, and it is the whole difference
between the sequential demonstration here and the concurrent load a later part of this project adds.

Three things every record carries, because the demonstration is built on them: the **tenant** the
request was authenticated as, the replica that actually **served** it, and the work and money the
response reports. Reading those back through the product's own boundary is what makes a claim about
spending something other than the application marking its own homework.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Final, Self

import httpx

from .auth import token_for
from .config import require_allowed_target
from .models import LedgerView

REQUEST_ID_HEADER: Final = "X-Request-Id"
REPLICA_HEADER: Final = "X-Limitless-Replica"

WORK_OPERATIONS: Final = frozenset({"enrich", "import_bundle"})
"""The operations that admit billable work. Everything else only ever reads."""


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """One request, as observed entirely through the product's own boundary."""

    sequence: int
    operation: str
    tenant_id: str
    addressed: str
    served_by: str | None
    status_code: int
    request_id: str
    input_bytes: int
    retry_after: str | None
    """The ``Retry-After`` header, when the response carried one. A constant, never a reset."""

    body: dict[str, Any] | None

    @property
    def succeeded(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def refused(self) -> bool:
        return self.status_code in (
            httpx.codes.REQUEST_ENTITY_TOO_LARGE,
            httpx.codes.TOO_MANY_REQUESTS,
            httpx.codes.SERVICE_UNAVAILABLE,
        )

    @property
    def admits_work(self) -> bool:
        """Whether this response reports work *this request* admitted.

        A usage read and a job read also carry ``cents_charged``, but theirs is an accumulated
        total rather than anything the request itself did. Adding those into a running total counts
        the same fictional money several times over and inflates the very number this whole project
        is about, so only the operations that actually admit work report any.
        """
        return self.operation in WORK_OPERATIONS

    @property
    def records_admitted(self) -> int:
        if not self.body or not self.admits_work:
            return 0
        return int(self.body.get("records_admitted", 0))

    @property
    def cents_charged(self) -> int:
        if not self.body or not self.admits_work:
            return 0
        return int(self.body.get("cents_charged", 0))


def replica_label(url: str) -> str:
    """A short human label for a replica base URL (``http://app-a:8000`` -> ``app-a``)."""
    return str(httpx.URL(url).host)


class HalyardHTTP:
    """Speaks to the addressed replicas. The caller chooses the ordering and the concurrency."""

    def __init__(
        self,
        replica_urls: tuple[str, ...],
        *,
        provider_url: str | None = None,
        timeout: float = 60.0,
        max_connections: int = 100,
    ) -> None:
        if not replica_urls:
            raise ValueError("at least one replica URL is required")
        # Validated again here so that no code path, however it was configured, can send a request
        # to a host that is not one of this demonstration's own services.
        self._replica_urls = tuple(require_allowed_target(url) for url in replica_urls)
        self._provider_url = require_allowed_target(provider_url) if provider_url else None
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
        await self._client.aclose()

    @property
    def replica_urls(self) -> tuple[str, ...]:
        return self._replica_urls

    @property
    def replica_labels(self) -> tuple[str, ...]:
        return tuple(replica_label(url) for url in self._replica_urls)

    def target_for(self, sequence: int) -> str:
        """Spread requests across the addressed replicas, round robin on the caller's sequence."""
        return self._replica_urls[(sequence - 1) % len(self._replica_urls)]

    async def send(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        sequence: int,
        tenant_id: str | None,
        authorization: str | None = None,
        json_body: Any | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> RequestRecord:
        base_url = self.target_for(sequence)
        request_id = f"{operation}-{sequence:05d}"
        request_headers = {REQUEST_ID_HEADER: request_id}
        if authorization is not None:
            request_headers["Authorization"] = authorization
        elif tenant_id is not None:
            request_headers["Authorization"] = f"Bearer {token_for(tenant_id)}"
        if headers:
            request_headers.update(headers)
        response = await self._client.request(
            method,
            f"{base_url}{path}",
            headers=request_headers,
            json=json_body,
            content=content,
        )
        try:
            body = response.json()
        except ValueError:
            body = None
        return RequestRecord(
            sequence=sequence,
            operation=operation,
            tenant_id=tenant_id or "-",
            addressed=replica_label(base_url),
            served_by=response.headers.get(REPLICA_HEADER),
            status_code=response.status_code,
            request_id=request_id,
            input_bytes=len(content) if content is not None else _json_size(json_body),
            retry_after=response.headers.get("Retry-After"),
            body=body if isinstance(body, dict) else None,
        )

    async def enrich(
        self, company_names: list[str], *, sequence: int, tenant_id: str
    ) -> RequestRecord:
        return await self.send(
            "POST",
            "/v1/enrich",
            operation="enrich",
            sequence=sequence,
            tenant_id=tenant_id,
            json_body={"records": [{"company_name": name} for name in company_names]},
        )

    async def enrich_raw(self, body: bytes, *, sequence: int, tenant_id: str) -> RequestRecord:
        """Submit an arbitrary body, for probing the body bound itself."""
        return await self.send(
            "POST",
            "/v1/enrich",
            operation="enrich",
            sequence=sequence,
            tenant_id=tenant_id,
            content=body,
            headers={"Content-Type": "application/json"},
        )

    async def list_records(
        self, *, sequence: int, tenant_id: str, page_size: int | str | None = None
    ) -> RequestRecord:
        query = "" if page_size is None else f"?page_size={page_size}"
        return await self.send(
            "GET",
            f"/v1/records{query}",
            operation="list_records",
            sequence=sequence,
            tenant_id=tenant_id,
        )

    async def import_bundle(self, bundle: bytes, *, sequence: int, tenant_id: str) -> RequestRecord:
        return await self.send(
            "POST",
            "/v1/imports",
            operation="import_bundle",
            sequence=sequence,
            tenant_id=tenant_id,
            content=bundle,
            headers={"Content-Type": "application/gzip"},
        )

    async def job(self, job_id: str, *, sequence: int, tenant_id: str) -> RequestRecord:
        return await self.send(
            "GET",
            f"/v1/jobs/{job_id}",
            operation="job",
            sequence=sequence,
            tenant_id=tenant_id,
        )

    async def usage(self, *, sequence: int, tenant_id: str) -> RequestRecord:
        return await self.send(
            "GET", "/v1/usage", operation="usage", sequence=sequence, tenant_id=tenant_id
        )

    async def probe_credential(self, *, sequence: int, authorization: str | None) -> RequestRecord:
        """Attempt a listing with a deliberately bad (or absent) credential."""
        return await self.send(
            "GET",
            "/v1/records",
            operation="auth_probe",
            sequence=sequence,
            tenant_id=None,
            authorization=authorization,
        )

    async def provider_ledger(self) -> LedgerView:
        """The provider's own bill, read from the provider itself."""
        if self._provider_url is None:
            raise RuntimeError("no provider URL was configured for this client")
        response = await self._client.get(f"{self._provider_url}/ledger")
        response.raise_for_status()
        return LedgerView.model_validate(response.json())

    async def set_provider_control(
        self, *, slow_mode: bool | None = None, held: bool | None = None
    ) -> None:
        """Set the provider fixture's instrumentation. Documented, and provider-side only."""
        if self._provider_url is None:
            raise RuntimeError("no provider URL was configured for this client")
        payload: dict[str, bool] = {}
        if slow_mode is not None:
            payload["slow_mode"] = slow_mode
        if held is not None:
            payload["held"] = held
        response = await self._client.post(f"{self._provider_url}/control", json=payload)
        response.raise_for_status()

    async def wait_until_ready(self, *, attempts: int = 60, delay: float = 1.0) -> None:
        """Block until every addressed replica reports healthy."""
        for base_url in self._replica_urls:
            for attempt in range(1, attempts + 1):
                try:
                    response = await self._client.get(f"{base_url}/healthz")
                    if response.status_code == httpx.codes.OK:
                        break
                except httpx.HTTPError:
                    pass
                if attempt == attempts:
                    raise RuntimeError(f"replica never became ready: {base_url}")
                await asyncio.sleep(delay)


def _json_size(payload: Any | None) -> int:
    if payload is None:
        return 0
    return len(json.dumps(payload, separators=(",", ":")).encode())
