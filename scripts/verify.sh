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
  docker compose --profile vulnerable down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

step() { printf '\n==> %s\n' "$1"; }

# Capture first, then match. Piping into `grep -q` under `set -o pipefail` is a trap: grep exits the
# moment it finds a match, the producer takes SIGPIPE, and the pipeline reports failure *because the
# check succeeded*. Every containment gate below would have been inverted by it.
contains() { printf '%s\n' "$1" | grep -qE "$2"; }

reseed() {
  docker compose run --rm --no-deps -T seed >/dev/null
}

# Start from nothing. A boundary that inherits whatever happened to be running already is not
# verifying this checkout, and the containment checks below would be reporting on somebody else's
# containers.
cleanup

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
rendered_config="$(docker compose --profile vulnerable config)"
if contains "$rendered_config" '^[[:space:]]+ports:'; then
  echo "a service publishes a port; nothing in this demonstration may be reachable from outside" >&2
  exit 1
fi
if ! contains "$rendered_config" 'internal: true'; then
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

step "containment: the vulnerable application is not started by the default path"
running_services="$(docker compose ps --services)"
if contains "$running_services" '^(vuln-a|vuln-b)$'; then
  echo "the vulnerable application was started by the default Compose path" >&2
  exit 1
fi
default_services="$(docker compose config --services)"
if contains "$default_services" '^(vuln-a|vuln-b)$'; then
  echo "the vulnerable application is not behind an opt-in profile" >&2
  exit 1
fi
profile_services="$(docker compose --profile vulnerable config --services)"
if ! contains "$profile_services" '^vuln-a$'; then
  echo "the vulnerable application is missing from its opt-in profile" >&2
  exit 1
fi
echo "not selected without its opt-in profile"

step "containment: the opt-in profile alone is not an acknowledgement"
if docker compose --profile vulnerable run --rm --no-deps -T -e ALLOW_VULNERABLE_DEMO= vuln-a \
     python -c "import limitless.vulnerable.app" >/dev/null 2>&1; then
  echo "the vulnerable application started without ALLOW_VULNERABLE_DEMO=true" >&2
  exit 1
fi
echo "refused to start without ALLOW_VULNERABLE_DEMO=true"

step "containment: no archive artifact is committed; the fixture is generated at build time"
tracked_files="$(git ls-files)"
if contains "$tracked_files" '\.(gz|zip|bz2|xz|tar|7z)$'; then
  echo "a compressed artifact is committed to the repository" >&2
  exit 1
fi
docker compose run --rm --no-deps -T harness \
  python -c "
import pathlib
from limitless import fixtures
from limitless.generate_expansion_fixture import check
bundle = pathlib.Path(fixtures.EXPANSION_FIXTURE_PATH).read_bytes()
failures = check(bundle)
if failures:
    raise SystemExit('\n'.join(failures))
print(f'the built fixture is {len(bundle)} B and passes its containment checks')
"

step "concurrent load harness against the secure application, two replicas, max concurrency"
install -d -m 0777 artifacts
# The harness's own documented ceiling, read from the code through a container rather than
# duplicated here — and without needing anything on the host but Docker.
MAX_CONCURRENCY="$(docker compose run --rm --no-deps -T verify \
  python -c 'from limitless.config import MAX_CONCURRENCY; print(MAX_CONCURRENCY)' | tr -dc '0-9')"
if [ -z "$MAX_CONCURRENCY" ]; then
  echo "could not read the harness concurrency ceiling from the code" >&2
  exit 1
fi
echo "the harness ceiling is ${MAX_CONCURRENCY} simultaneous requests"
docker compose run --rm --no-deps -T \
  -e LIMITLESS_CONCURRENCY="$MAX_CONCURRENCY" harness \
  python -m limitless.harness --mode natural

step "concurrent load harness against the secure application, one replica, max concurrency"
docker compose run --rm --no-deps -T \
  -e LIMITLESS_REPLICAS=1 -e LIMITLESS_CONCURRENCY="$MAX_CONCURRENCY" \
  -e LIMITLESS_TRANSCRIPT_PATH=/artifacts/harness-transcript-one-replica.txt harness \
  python -m limitless.harness --mode natural

step "the harness accepts no arbitrary target"
if docker compose run --rm --no-deps -T \
     -e LIMITLESS_REPLICA_URLS=http://example.com:8000 harness >/dev/null 2>&1; then
  echo "the harness accepted a target that is not one of this demonstration's own services" >&2
  exit 1
fi
echo "refused a target outside this demonstration's own services"

step "starting the vulnerable application (both opt-in actions, and only now)"
ALLOW_VULNERABLE_DEMO=true docker compose --profile vulnerable up --detach --wait vuln-a vuln-b

step "vulnerable ladder, deterministic mode — every shape is REQUIRED to reproduce"
docker compose run --rm --no-deps -T \
  -e LIMITLESS_TRANSCRIPT_PATH=/artifacts/vulnerable-ladder.txt harness \
  python -m limitless.harness --variant vulnerable

step "restoring the secure baseline before the suite runs"
reseed

step "ruff, mypy, and the test suite, through the same boundary"
reseed
ALLOW_VULNERABLE_DEMO=true docker compose run --rm --no-deps \
  -e LIMITLESS_REQUIRE_VULNERABLE=1 verify

printf '\n==> verification complete\n'
