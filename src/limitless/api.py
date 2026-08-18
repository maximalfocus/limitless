"""The HTTP boundary both application variants share.

Everything here is identical between the secure application and the later unbounded one: the routes'
shapes, the credentials, the read views, the refusal responses, the audit event, and above all the
**success payloads**. That is deliberate. The two variants must be indistinguishable to a client
doing ordinary business, so the only difference a reader can find is *how much work a caller is
allowed to name* — which is why the interesting code lives in ``secure/`` and the boring code lives
here, once.

A refusal says nothing about why. Every over-large input — body, batch, page size, decompressed
bytes, expansion ratio — is the same ``413`` with the same body. An exhausted allowance is a
``429`` with a **fixed** ``Retry-After`` that is not the real reset instant. A saturated in-flight
cap is a ``503``. Missing, malformed, unknown, and expired credentials are the same ``401``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Final
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response, status

from . import audit, store
from .auth import authenticate
from .config import AppConfig
from .db import Conn, ConnPool
from .models import HealthResponse, JobView, UsageView
from .refusal import LimitReachedError, RefusalKind

UsageSource = Callable[[Conn, str], Awaitable[UsageView | None]]
"""Where a variant reads a tenant's spend from.

The two variants answer this endpoint with the same shape and, for the same legitimate work, the
same numbers — but they keep the record in different places, because one has a bound to maintain
and the other has only an obligation to remember.
"""

REQUEST_ID_HEADER: Final = "X-Request-Id"
REPLICA_HEADER: Final = "X-Limitless-Replica"

DETAIL_UNAUTHORIZED: Final = "unauthorized"
DETAIL_NOT_FOUND: Final = "not found"
DETAIL_BAD_REQUEST: Final = "bad request"
DETAIL_REFUSED: Final = "request could not be completed"
"""One body for every refusal, whichever limit produced it."""

REFUSAL_STATUS: Final[dict[RefusalKind, int]] = {
    RefusalKind.INPUT_TOO_LARGE: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    RefusalKind.ALLOWANCE_EXHAUSTED: status.HTTP_429_TOO_MANY_REQUESTS,
    RefusalKind.CAPACITY_SHED: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=DETAIL_UNAUTHORIZED,
        headers={"WWW-Authenticate": "Bearer"},
    )


def bad_request() -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=DETAIL_BAD_REQUEST)


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=DETAIL_NOT_FOUND)


def require_tenant(authorization: str | None) -> str:
    """The server-derived principal. Never a header, body field, or query parameter."""
    tenant_id = authenticate(authorization)
    if tenant_id is None:
        raise unauthorized()
    return tenant_id


def pool_of(request: Request) -> ConnPool:
    pool: ConnPool = request.app.state.pool
    return pool


def stamp_requests(app: FastAPI, replica_name: str) -> None:
    """Give every request a correlation id and every response the replica that served it."""

    @app.middleware("http")
    async def stamp(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[REPLICA_HEADER] = replica_name
        return response


def install_refusal_handler(app: FastAPI, settings: AppConfig) -> None:
    """Turn every :class:`LimitReachedError` into one audit event and one uniform response."""

    @app.exception_handler(LimitReachedError)
    async def on_limit_reached(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, LimitReachedError)
        request_id = getattr(request.state, "request_id", "-")
        tenant_id = getattr(request.state, "tenant_id", "-")
        audit.emit_refusal(
            request_id=request_id,
            replica=settings.replica_name,
            operation=exc.operation,
            tenant_id=tenant_id,
        )
        headers: dict[str, str] = {}
        if exc.kind is RefusalKind.ALLOWANCE_EXHAUSTED:
            # A constant, not the true reset instant: a caller must not be able to read the shape
            # of the allowance out of a refusal.
            headers["Retry-After"] = str(settings.bounds.retry_after_seconds)
        raise HTTPException(
            status_code=REFUSAL_STATUS[exc.kind],
            detail=DETAIL_REFUSED,
            headers=headers or None,
        ) from exc


def add_common_routes(
    app: FastAPI, settings: AppConfig, *, variant: str, usage_source: UsageSource
) -> None:
    """Health, the cheap job endpoint, and the tenant's own usage — identical across variants."""

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz(request: Request) -> HealthResponse:
        async with pool_of(request).connection() as conn:
            await conn.execute("SELECT 1")
        return HealthResponse(status="ok", replica=settings.replica_name, variant=variant)

    @app.get("/v1/jobs/{job_id}", response_model=JobView)
    async def get_job(
        job_id: UUID,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> JobView:
        """The cheap endpoint.

        It touches no provider, holds no allowance, and occupies no in-flight slot. It is here to be
        the bystander: an endpoint with no defect of its own, whose availability depends entirely on
        whether some *other* endpoint was allowed to consume everything.
        """
        tenant_id = require_tenant(authorization)
        request.state.tenant_id = tenant_id
        async with pool_of(request).connection() as conn:
            view = await store.read_job(conn, job_id=job_id, tenant_id=tenant_id)
        if view is None:
            raise not_found()
        return view

    @app.get("/v1/usage", response_model=UsageView)
    async def get_usage(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> UsageView:
        """What this tenant has spent — never what it has left."""
        tenant_id = require_tenant(authorization)
        request.state.tenant_id = tenant_id
        async with pool_of(request).connection() as conn:
            view = await usage_source(conn, tenant_id)
        if view is None:
            raise not_found()
        return view
