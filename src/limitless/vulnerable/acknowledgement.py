"""The second of the two deliberate actions required to start the unbounded application.

Selecting the opt-in Compose profile is the first. It is not enough on its own, and that is the
whole point: a profile is something you enable once and forget, so it makes a poor acknowledgement.
This module refuses to let the module graph finish loading unless somebody has also said, in so many
words, that they know what they are starting.

The check runs at **import** time rather than at request time. An application that starts and
then refuses requests is still a running unbounded application; one that will not import is not
running at all.
"""

from __future__ import annotations

import os
from typing import Final

ACKNOWLEDGEMENT_VARIABLE: Final = "ALLOW_VULNERABLE_DEMO"
ACKNOWLEDGEMENT_VALUE: Final = "true"

REFUSAL: Final = (
    f"Refusing to start the intentionally vulnerable application.\n"
    f"\n"
    f"This service has no bounds on the work a caller may name. It is local educational material\n"
    f"and must never be deployed or exposed.\n"
    f"\n"
    f"Starting it takes two deliberate actions, and the opt-in Compose profile is only the first.\n"
    f"Set {ACKNOWLEDGEMENT_VARIABLE}={ACKNOWLEDGEMENT_VALUE} to acknowledge the second."
)


def acknowledged(env: dict[str, str] | None = None) -> bool:
    """Whether the acknowledgement has been given, exactly and without interpretation."""
    source = os.environ if env is None else env
    return source.get(ACKNOWLEDGEMENT_VARIABLE, "").strip().lower() == ACKNOWLEDGEMENT_VALUE


def require_acknowledgement(env: dict[str, str] | None = None) -> None:
    """Stop the import here unless the acknowledgement has been given."""
    if not acknowledged(env):
        raise RuntimeError(REFUSAL)
