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
from enum import StrEnum
from pathlib import Path
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

    max_in_flight_upstream: int = 48
    """Concurrent provider calls per replica. Excess is shed immediately rather than queued.

    Chosen to sit comfortably above ``MAX_CONCURRENCY``, so that the harness's highest configured
    load — even when every request is aimed at a single replica — is served rather than shed. That
    is a requirement rather than a courtesy: a heavy but legitimate tenant must be able to spend its
    whole allowance without being refused, so a capacity control that turned ordinary concurrency
    away would be protecting the budget by rejecting valid work.

    This cap is **per process**, and deliberately so. Two replicas admit twice as much in-flight
    work, because each one is protecting its own workers, memory, and connections — which is what a
    concurrency limit is for. That is not the mistake a later part of this demonstration is about:
    there, the thing held in process memory is the *budget*, and a budget enforced twice in parallel
    is simply not the budget. Money is shared state and lives in the store; capacity is local and
    lives in the process.
    """

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


class ReproductionMode(StrEnum):
    """How a run reproduces what it reports."""

    NATURAL = "natural"
    """No instrumentation whatsoever, in any code path, and no provider hold.

    Genuine concurrent load, and figures that are **observed**. Against the secure application its
    assertions are still exact — zero bound violations, zero cheap-endpoint failures, the global cap
    never breached — because a secure-side violation is a real failure rather than a flake.
    """


def parse_reproduction_mode(raw: str | None) -> ReproductionMode | None:
    """Parse a mode name. Returns the default for ``None``/empty and ``None`` for a bad value."""
    if raw is None or raw == "":
        return ReproductionMode.NATURAL
    try:
        return ReproductionMode(raw.strip().lower())
    except ValueError:
        return None


DEFAULT_CONCURRENCY: Final = 24
"""Concurrent requests per burst by default."""

MAX_CONCURRENCY: Final = 32
"""A hard ceiling on concurrency.

The load exists only to exercise an unbounded code path, is aimed only at the demonstration's own
services on a network with no egress, and is capped by explicit configuration. This is a containment
bound, not a tuning knob: nothing here may grow into a general-purpose load tool.
"""

DEFAULT_ROUNDS: Final = 3
MAX_ROUNDS: Final = 20
"""A hard ceiling on how many times a burst may be repeated."""

DEFAULT_BATCH_RECORDS: Final = 20
"""Records named by one ordinary concurrent request. Each one is a metered lookup."""

MAX_LOOKUPS_PER_ROUND: Final = 2_000
"""A hard ceiling on the **work** one round may name, not merely on how many requests it sends.

This is the same distinction the whole demonstration is about, turned on the demonstration itself.
A bound on request count says nothing about how much work those requests name: nineteen requests can
name five hundred lookups each and ask a single-CPU fixture for nine and a half thousand lookups at
once. Bounding the work keeps the harness's own load modest and — just as importantly — keeps every
required assertion independent of how fast the host happens to be. A run whose outcome depends on
whether a deadline was reached is not a reproducible run.
"""


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


def _bounded_int(source: dict[str, str] | Any, name: str, default: int, ceiling: int) -> int:
    """Read a bounded run parameter, refusing anything outside its documented range."""
    raw = source.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; got {raw!r}") from exc
    if not 1 <= value <= ceiling:
        raise ValueError(f"{name} must be between 1 and {ceiling}; got {value}")
    return value


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """Configuration for the concurrent load harness.

    The concurrency level and the round count are demonstration parameters and explicit safety
    bounds at the same time. The load is generated only to expose an unbounded code path, is aimed
    only at the demonstration's own in-network services, and can never exceed the ceilings above.
    """

    runner: RunnerConfig
    mode: ReproductionMode
    concurrency: int
    rounds: int
    batch_records: int
    transcript_path: Path

    @property
    def in_flight_capacity(self) -> int:
        """The in-flight capacity the addressed replicas have between them.

        Each replica bounds its own in-flight upstream work, so the capacity available to a run is
        the per-replica cap times the number of replicas being addressed.
        """
        return BOUNDS.max_in_flight_upstream * len(self.runner.replica_urls)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> HarnessConfig:
        source = os.environ if env is None else env
        mode = parse_reproduction_mode(source.get("LIMITLESS_MODE"))
        if mode is None:
            raise ValueError(
                f"LIMITLESS_MODE must be one of {', '.join(m.value for m in ReproductionMode)}; "
                f"got {source.get('LIMITLESS_MODE')!r}"
            )
        return cls(
            runner=RunnerConfig.from_env(env),
            mode=mode,
            concurrency=_bounded_int(
                source, "LIMITLESS_CONCURRENCY", DEFAULT_CONCURRENCY, MAX_CONCURRENCY
            ),
            rounds=_bounded_int(source, "LIMITLESS_ROUNDS", DEFAULT_ROUNDS, MAX_ROUNDS),
            batch_records=_bounded_int(
                source,
                "LIMITLESS_BATCH_RECORDS",
                DEFAULT_BATCH_RECORDS,
                BOUNDS.max_batch_items,
            ),
            transcript_path=Path(
                source.get("LIMITLESS_TRANSCRIPT_PATH", "/artifacts/harness-transcript.txt")
            ),
        )
