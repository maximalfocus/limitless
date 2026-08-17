"""Run parameters and the secure application's documented bounds.

The bounds in :class:`SecureBounds` are the product of this demonstration. Each one is written here
as an explicit, named, server-side maximum rather than left to a framework default or a web server's
configuration, because a reader has to be able to point at the line that says how much work a caller
may name. They cover all five dimensions a caller can otherwise drive without limit:

============  ==========================================================================
how big       ``max_body_bytes`` / ``max_import_body_bytes`` — enforced *while reading*
how many      ``max_batch_items`` and ``max_page_size``
how much      ``max_decompressed_bytes`` and ``max_expansion_ratio`` — enforced *during*
              decompression
how often     the per-tenant allowance, charged in lookups (see ``fixtures``)
how many at   ``max_in_flight_upstream``, with excess shed rather than queued, plus
once          ``upstream_deadline_seconds`` and ``request_deadline_seconds``, which cancel
============  ==========================================================================

``LIMITLESS_REPLICAS`` selects how many application replicas a runner addresses. The number of
processes sharing the state is part of the mechanism this project exists to teach, so it is a
first-class documented run parameter and not a deployment knob.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

DEFAULT_DATABASE_URL: Final = "postgresql://limitless:limitless-demo-password@db:5432/limitless"
DEFAULT_PROVIDER_URL: Final = "http://coastwise:8000"
DEFAULT_REPLICA_URLS: Final = ("http://app-a:8000", "http://app-b:8000")
DEFAULT_VULNERABLE_REPLICA_URLS: Final = ("http://vuln-a:8000", "http://vuln-b:8000")

ALLOWED_TARGET_HOSTS: Final = frozenset(
    {"app-a", "app-b", "vuln-a", "vuln-b", "coastwise"},
)
"""Every host this project is ever permitted to send a request to.

This is a containment requirement, not a convenience. The demonstration generates load in order to
expose an unbounded code path, and it must never be usable as a general-purpose load or stress tool:
its destinations are the demonstration's own in-network services, and no configuration value,
argument, or environment variable can redirect it at anything else.
"""


def require_allowed_target(url: str) -> str:
    """Return ``url`` if it names one of the demonstration's own services, else raise.

    Rejecting rather than filtering is deliberate: a silently ignored target would let someone
    believe they had pointed this at another host.
    """
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ALLOWED_TARGET_HOSTS:
        raise ValueError(
            f"{url!r} is not one of this demonstration's own services; "
            f"permitted hosts are {', '.join(sorted(ALLOWED_TARGET_HOSTS))}. "
            f"This project cannot be pointed at any other host."
        )
    return url


@dataclass(frozen=True, slots=True)
class SecureBounds:
    """Every documented maximum the secure application enforces, in one place."""

    max_body_bytes: int = 65_536
    """64 KiB. Enforced while reading the request stream, before the body is buffered."""

    max_import_body_bytes: int = 262_144
    """256 KiB. An import bundle may legitimately be larger than a JSON batch — and no larger."""

    max_batch_items: int = 500
    """How many records one enrichment batch may name. Each one is a metered lookup."""

    max_page_size: int = 200
    """How many stored records one listing may ask for, whatever the caller supplies."""

    max_decompressed_bytes: int = 4_194_304
    """4 MiB. An absolute ceiling on what a bundle may become, checked *during* decompression."""

    max_expansion_ratio: int = 25
    """The second expansion ceiling. A ratio bound catches what an absolute bound alone cannot."""

    max_in_flight_upstream: int = 8
    """Concurrent provider calls. Excess is shed immediately rather than queued without bound."""

    upstream_deadline_seconds: float = 5.0
    """A deadline on each provider call that *cancels* the call rather than merely returning."""

    request_deadline_seconds: float = 20.0
    """A deadline on the request as a whole, which likewise cancels the work beneath it."""

    retry_after_seconds: int = 60
    """A fixed, generic ``Retry-After``.

    Deliberately a constant rather than the true reset instant: a caller must not be able to read
    the shape of the allowance out of a refusal.
    """


BOUNDS: Final = SecureBounds()
"""The bounds the secure application runs with. One instance, so tests and docs cannot drift."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Configuration for one application replica."""

    database_url: str
    provider_url: str
    replica_name: str
    pool_min_size: int
    pool_max_size: int
    lookup_price_cents: int
    """What the application expects a lookup to cost, so it can reserve before the work happens.

    Read from the same variable the provider fixture reads, so the reservation and the bill cannot
    silently disagree about the price of the thing being reserved.
    """

    bounds: SecureBounds

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AppConfig:
        source = os.environ if env is None else env
        return cls(
            database_url=source.get("LIMITLESS_DATABASE_URL", DEFAULT_DATABASE_URL),
            provider_url=require_allowed_target(
                source.get("LIMITLESS_PROVIDER_URL", DEFAULT_PROVIDER_URL)
            ),
            replica_name=source.get("LIMITLESS_REPLICA_NAME", "app-a"),
            pool_min_size=int(source.get("LIMITLESS_POOL_MIN_SIZE", "2")),
            pool_max_size=int(source.get("LIMITLESS_POOL_MAX_SIZE", "16")),
            lookup_price_cents=int(
                source.get("LIMITLESS_LOOKUP_PRICE_CENTS", str(_default_price()))
            ),
            bounds=BOUNDS,
        )


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Configuration for the Coastwise Registry fixture.

    ``slow_mode_delay_seconds`` and the hold/release control are *instrumentation*. They change only
    when the provider answers, never whether the application under study bounded its own work.
    """

    lookup_price_cents: int
    slow_mode_delay_seconds: float

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ProviderConfig:
        source = os.environ if env is None else env
        return cls(
            lookup_price_cents=int(
                source.get("LIMITLESS_LOOKUP_PRICE_CENTS", str(_default_price()))
            ),
            slow_mode_delay_seconds=float(
                source.get("LIMITLESS_PROVIDER_SLOW_DELAY_SECONDS", "2.0")
            ),
        )


def _default_price() -> int:
    from . import fixtures

    return fixtures.LOOKUP_PRICE_CENTS


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """Configuration for a process that drives the demonstration from outside the application."""

    database_url: str
    provider_url: str
    replica_urls: tuple[str, ...]
    """The replicas actually addressed, already narrowed to ``LIMITLESS_REPLICAS`` entries."""

    vulnerable_replica_urls: tuple[str, ...]
    """The vulnerable replicas, narrowed the same way. Empty unless that opt-in profile is up."""

    request_timeout_seconds: float

    def urls_for(self, variant: str) -> tuple[str, ...]:
        return self.vulnerable_replica_urls if variant == "vulnerable" else self.replica_urls

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RunnerConfig:
        source = os.environ if env is None else env
        available = _target_list(source, "LIMITLESS_REPLICA_URLS", DEFAULT_REPLICA_URLS)
        if not available:
            raise ValueError("LIMITLESS_REPLICA_URLS must name at least one replica")
        vulnerable = _target_list(
            source, "LIMITLESS_VULNERABLE_REPLICA_URLS", DEFAULT_VULNERABLE_REPLICA_URLS
        )
        replicas = int(source.get("LIMITLESS_REPLICAS", str(len(available))))
        if not 1 <= replicas <= len(available):
            raise ValueError(
                f"LIMITLESS_REPLICAS must be between 1 and {len(available)}; got {replicas}"
            )
        return cls(
            database_url=source.get("LIMITLESS_DATABASE_URL", DEFAULT_DATABASE_URL),
            provider_url=require_allowed_target(
                source.get("LIMITLESS_PROVIDER_URL", DEFAULT_PROVIDER_URL)
            ),
            replica_urls=available[:replicas],
            vulnerable_replica_urls=vulnerable[:replicas],
            request_timeout_seconds=float(source.get("LIMITLESS_REQUEST_TIMEOUT_SECONDS", "60")),
        )


def _target_list(
    source: dict[str, str] | Any, name: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    """Parse a comma-separated target list, refusing any host that is not our own."""
    raw = source.get(name, ",".join(default))
    return tuple(require_allowed_target(url.strip()) for url in raw.split(",") if url.strip())
