"""Container healthcheck: ask this container's own ``/healthz`` and exit 0 or 1.

Uses only the standard library so the runtime image needs no extra tool, and talks to loopback so it
works on a network with no egress.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    url = os.environ.get("LIMITLESS_HEALTHCHECK_URL", "http://127.0.0.1:8000/healthz")
    try:
        # Fixed loopback URL; the container talks only to itself.
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status != 200:
                return 1
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return 1
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
