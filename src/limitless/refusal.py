"""One refusal type for every limit the application enforces.

A refusal carries the **kind** of resource that ran out and nothing else — never which bound, never
by how much, never what remains. The kind exists only because the HTTP contract requires materially
different coarse statuses for "you named more than is allowed" (``413``), "your allowance is spent"
(``429``), and "there is no capacity right now" (``503``); a client that could not tell those apart
could not behave correctly.

Within each kind, refusals are indistinguishable. A body one byte too large, a batch naming a
hundred thousand records, a page size of a million, a bundle that crossed the decompressed-byte
ceiling, and a bundle that crossed the expansion ratio are **all** the same ``413`` with the same
body and the same audit event. That family is where an oracle would actually pay: a caller who could
tell which of those five bounds refused them, and by how much, could map every bound in a handful of
probes. They cannot.
"""

from __future__ import annotations

from enum import StrEnum

from .audit import RefusedOperation


class RefusalKind(StrEnum):
    """The coarse category of a refusal — never the bound that produced it."""

    INPUT_TOO_LARGE = "input_too_large"
    """The caller named more work than is allowed, in any dimension. One status for all of them."""

    ALLOWANCE_EXHAUSTED = "allowance_exhausted"
    """The tenant's own partition of the fictional budget, or the company's cap, has no room."""

    CAPACITY_SHED = "capacity_shed"
    """In-flight upstream work is at its cap. Excess is shed at once rather than queued."""


class LimitReachedError(Exception):
    """Raised wherever a bound, quota, or capacity limit refuses a request.

    Deliberately carries no detail beyond the coarse kind and the endpoint, because everything it
    could usefully carry is something a caller must not learn.
    """

    def __init__(self, kind: RefusalKind, operation: RefusedOperation) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.operation = operation
