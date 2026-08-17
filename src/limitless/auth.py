"""Demo-only bearer authentication.

Authentication exists here for exactly two reasons, and no others:

1. it gives the quota a **server-derived principal** to be keyed on. A limiter keyed on anything the
   caller supplies is a limiter the caller can mint a fresh bucket of at will, so the tenant a
   charge lands on must come from the credential rather than from a header, a body field, or a query
   parameter; and
2. it makes the "every request in this attack is authenticated and authorized" boundary
   demonstrable. The attacking tenant is an ordinary paying customer in good standing throughout.

Every token is a conspicuously fake constant compiled into the image. Missing, malformed, unknown,
and expired credentials are rejected identically, so a response carries no information about *why* a
credential failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from . import fixtures

TOKEN_PREFIX: Final = "limitless-demo-token-"
EXPIRED_TOKEN: Final = "limitless-demo-token-expired"
EXPIRED_AT: Final = datetime(2020, 1, 1, tzinfo=UTC)
"""A fixed instant in the past, so "expired" is deterministic rather than clock-dependent."""

UNKNOWN_TOKEN: Final = "limitless-demo-token-not-issued"
"""A well-formed token that was never issued, used by the demonstration and by tests."""


@dataclass(frozen=True, slots=True)
class DemoToken:
    """A fictional bearer credential belonging to a fictional tenant."""

    token: str
    tenant_id: str
    expires_at: datetime | None


def token_for(tenant_id: str) -> str:
    """The fictional bearer token for a fictional tenant."""
    return f"{TOKEN_PREFIX}{tenant_id.lower()}"


def _build_registry() -> dict[str, DemoToken]:
    registry = {
        token_for(tenant.tenant_id): DemoToken(token_for(tenant.tenant_id), tenant.tenant_id, None)
        for tenant in fixtures.TENANTS
        if tenant.tenant_id != fixtures.EXPIRED_TENANT_ID
    }
    registry[EXPIRED_TOKEN] = DemoToken(EXPIRED_TOKEN, fixtures.EXPIRED_TENANT_ID, EXPIRED_AT)
    return registry


TOKENS: Final[dict[str, DemoToken]] = _build_registry()


def authenticate(authorization_header: str | None, *, now: datetime | None = None) -> str | None:
    """Return the fictional tenant id for a valid header, or ``None`` for every failure mode.

    Deliberately one return value for missing, malformed, unknown, and expired credentials: the
    caller must not be able to tell those apart.
    """
    if not authorization_header:
        return None
    scheme, _, presented = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        return None
    record = TOKENS.get(presented.strip())
    if record is None:
        return None
    if record.expires_at is not None and record.expires_at <= (now or datetime.now(UTC)):
        return None
    return record.tenant_id
