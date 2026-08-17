#!/usr/bin/env bash
# The complete verification boundary. Local runs and GitHub Actions invoke exactly this script, so
# "green on my machine" and "green in CI" mean the same thing.
#
#   bash scripts/verify.sh
#
# Requires Docker and nothing else: no PostgreSQL, no Python environment, no host tuning.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

step() { printf '\n==> %s\n' "$1"; }

reseed() {
  docker compose run --rm --no-deps -T seed >/dev/null
}

step "building images"
docker compose build

step "starting the topology (two replicas, one database, the provider fixture, no egress)"
docker compose up --detach --wait db coastwise app-a app-b

step "sequential demonstration, addressing two replicas"
demo_output="$(mktemp)"
docker compose run --rm --no-deps -T demo | tee "$demo_output"

summary_line="$(grep 'limitless-demo-summary:' "$demo_output" | tail -n 1)"
refusals="$(printf '%s' "$summary_line" | sed -n 's/.*"refusals":[[:space:]]*\([0-9][0-9]*\).*/\1/p')"
if [ -z "${refusals}" ]; then
  echo "could not read the refusal count from the demonstration summary" >&2
  exit 1
fi

step "audit gate: exactly ${refusals} generic refusal events, and no token, in the app logs"
docker compose logs --no-log-prefix app-a app-b \
  | docker compose run --rm --no-deps -T verify python -m limitless.auditcheck --expected "$refusals"

step "sequential demonstration, addressing one replica (the run parameter is real)"
reseed
docker compose run --rm --no-deps -T -e LIMITLESS_REPLICAS=1 demo >/dev/null
echo "one-replica run completed successfully"

step "containment: nothing is published, and the network has no egress"
if docker compose config | grep -qE '^\s+ports:'; then
  echo "a service publishes a port; nothing in this demonstration may be reachable from outside" >&2
  exit 1
fi
if ! docker compose config | grep -q 'internal: true'; then
  echo "the demo network is not internal; this demonstration must have no egress" >&2
  exit 1
fi
if docker compose run --rm --no-deps -T demo \
     python -c "
import socket, sys
socket.setdefaulttimeout(5)
try:
    socket.create_connection(('example.com', 80))
except OSError:
    sys.exit(0)
sys.exit(1)
"; then
  echo "no egress from the demo network"
else
  echo "the demo network reached a host outside itself" >&2
  exit 1
fi

step "containment: every service declares an explicit memory and CPU limit"
docker compose run --rm --no-deps -T verify python - <<'PY'
import sys, yaml
config = yaml.safe_load(open("docker-compose.yml"))
missing = [
    name
    for name, service in config["services"].items()
    if not service.get("deploy", {}).get("resources", {}).get("limits", {}).get("memory")
    or not service.get("deploy", {}).get("resources", {}).get("limits", {}).get("cpus")
]
if missing:
    print(f"services without an explicit memory and CPU limit: {missing}", file=sys.stderr)
    sys.exit(1)
print("every service declares an explicit memory and CPU limit")
PY

step "containment: no vulnerable entry point and no load harness exists yet"
if docker compose config --services | grep -qE '^(vuln-a|vuln-b|harness)$'; then
  echo "a vulnerable service or a load harness is defined; this slice must not introduce one" >&2
  exit 1
fi
if find src -name '*.py' | grep -qE '/(vulnerable|harness)/'; then
  echo "vulnerable or harness source exists; this slice must not introduce it" >&2
  exit 1
fi
echo "the secure application is the only application, and it is the default"

step "ruff, mypy, and the test suite, through the same boundary"
reseed
docker compose run --rm --no-deps verify

printf '\n==> verification complete\n'
