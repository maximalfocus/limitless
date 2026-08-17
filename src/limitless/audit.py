"""The rejection audit event.

When the secure application refuses a request because a bound, quota, or capacity limit was reached,
it emits exactly one structured JSON event to standard output.

The event is deliberately **generic**. It identifies the refused request and its outcome so an
operator can correlate it, and it says nothing else: not which bound was crossed, not by how much,
not how much allowance remains, not when the allowance resets, not how many slots are occupied, and
never a token or a personal datum. A body that was one byte too large, a batch that named a hundred
thousand records, an exhausted allowance, and a saturated in-flight cap all produce byte-identical
events apart from the request they name.

That is the point, and it is a security property rather than a stylistic one. A caller who can tell
*which* limit refused a request, or *how far* over it went, can map the limits with a handful of
probes and then plan around them. Neither the log nor the client response gives them anything to
work with.
"""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from typing import Final

EVENT_NAME: Final = "limit_refusal"
REFUSAL_REASON: Final = "limit_reached"
"""One reason for every refusal. Which limit, and by how much, stays out of the record."""


class RefusedOperation(StrEnum):
    """The endpoint that refused — never the bound that refused it."""

    ENRICH = "enrich"
    LIST_RECORDS = "list_records"
    IMPORT_BUNDLE = "import_bundle"


def emit_refusal(
    *,
    request_id: str,
    replica: str,
    operation: RefusedOperation,
    tenant_id: str,
) -> None:
    """Write exactly one generic refusal event as a single JSON line on standard output."""
    event = {
        "event": EVENT_NAME,
        "request_id": request_id,
        "replica": replica,
        "operation": operation.value,
        "tenant_id": tenant_id,
        "outcome": "refused",
        "reason": REFUSAL_REASON,
    }
    sys.stdout.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()
