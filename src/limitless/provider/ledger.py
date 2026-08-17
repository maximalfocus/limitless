"""Coastwise Registry's own books.

The provider keeps this ledger itself and reports it through its own endpoint, which is the single
most important structural decision in this demonstration. The cost of an attack is read off the
**provider's** bill rather than asserted by the application under study, so a claim that one request
spent four fifths of the monthly budget is not the application marking its own homework.

Every amount here is fictional money at a fictional provider, and the ledger is rebuilt from nothing
on every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import fixtures
from ..models import LedgerView, TenantLedgerEntry


@dataclass(slots=True)
class Ledger:
    """Lookups performed and fictional cents charged, per tenant and in total."""

    price_cents_per_lookup: int
    _lookups: dict[str, int] = field(default_factory=dict)

    def charge(self, tenant_id: str, lookups: int) -> int:
        """Record ``lookups`` performed for ``tenant_id`` and return the fictional cents charged."""
        if lookups < 0:
            raise ValueError(f"cannot charge {lookups} lookups")
        self._lookups[tenant_id] = self._lookups.get(tenant_id, 0) + lookups
        return lookups * self.price_cents_per_lookup

    def reset(self) -> None:
        """Start a run from an empty bill."""
        self._lookups.clear()

    @property
    def total_lookups(self) -> int:
        return sum(self._lookups.values())

    @property
    def total_cents(self) -> int:
        return self.total_lookups * self.price_cents_per_lookup

    def view(self) -> LedgerView:
        return LedgerView(
            provider=fixtures.PROVIDER_NAME,
            currency=fixtures.CURRENCY_LABEL,
            price_cents_per_lookup=self.price_cents_per_lookup,
            total_lookups=self.total_lookups,
            total_cents=self.total_cents,
            per_tenant=[
                TenantLedgerEntry(
                    tenant_id=tenant_id,
                    lookups=lookups,
                    cents=lookups * self.price_cents_per_lookup,
                )
                for tenant_id, lookups in sorted(self._lookups.items())
            ],
        )
