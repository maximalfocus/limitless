#!/usr/bin/env bash
# One-shot disposable demonstration. Requires Docker and nothing else.
#
#   bash scripts/demo.sh
#
# Optional run parameter:
#   LIMITLESS_REPLICAS=1 bash scripts/demo.sh   # address one replica instead of two
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> building images (nothing is installed on the host)"
docker compose build

echo "==> starting the topology and running the sequential demonstration"
docker compose run --rm demo
