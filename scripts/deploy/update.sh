#!/usr/bin/env bash
# One-command production update for the Vanguard VM.
#
# Runs on the GCP VM from anywhere; it resolves the repo root from its own
# location. Mirrors the runbook (docs/deployment/README.md Phase 15):
#   git pull -> (source dashboard build-args) -> docker compose up -> health check
#
# Usage (on the VM):
#   ./scripts/deploy/update.sh                 # pull main, rebuild all, verify
#   ./scripts/deploy/update.sh --recreate      # env-only change: recreate, no image build
#   ./scripts/deploy/update.sh --service backend   # scope to one service
#   ./scripts/deploy/update.sh --recreate --service backend  # backend secret/env change
#   ./scripts/deploy/update.sh --branch hotfix # deploy a different branch
#   ./scripts/deploy/update.sh --no-pull       # deploy the current checkout as-is
#   ./scripts/deploy/update.sh --tag           # tag current :prod images before deploying (rollback point)
#
# Exit codes: 0 ok, non-zero on any failure (safe to use in cron/CI).

set -euo pipefail

# --- Config -----------------------------------------------------------------
COMPOSE_FILE="docker-compose.prod.yml"
DASHBOARD_ENV="deploy/dashboard.env.production"
BACKEND_ENV="deploy/.env.production"
HEALTH_URL="https://ezfi.smilesuite.ai/health"
READY_URL="https://ezfi.smilesuite.ai/ready"
DASHBOARD_URL="https://ezfi.smilesuite.ai/"
BACKEND_CONTAINER="vanguard-backend"
DEFAULT_BRANCH="main"
COMPOSE_WAIT_TIMEOUT=120

# --- Args -------------------------------------------------------------------
BRANCH="$DEFAULT_BRANCH"
DO_PULL=1
DO_BUILD=1
DO_TAG=0
SERVICE=""

usage() { sed -n '2,20p' "$0"; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		--recreate|-r) DO_BUILD=0 ;;
		--service|-s) SERVICE="${2:?--service needs a name}"; shift ;;
		--branch|-b) BRANCH="${2:?--branch needs a name}"; shift ;;
		--no-pull) DO_PULL=0 ;;
		--tag) DO_TAG=1 ;;
		-h|--help) usage 0 ;;
		*) echo "Unknown option: $1" >&2; usage 1 ;;
	esac
	shift
done

# --- Locate repo root (script lives in scripts/deploy/) ---------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --- Preflight --------------------------------------------------------------
command -v docker >/dev/null || die "docker not found on PATH"
docker compose version >/dev/null 2>&1 || die "'docker compose' plugin not available"
[[ -f "$COMPOSE_FILE" ]] || die "$COMPOSE_FILE not found in $REPO_ROOT"
[[ -f "$BACKEND_ENV" ]] || die "$BACKEND_ENV missing (never committed; create it on the VM)"
[[ -f "$DASHBOARD_ENV" ]] || die "$DASHBOARD_ENV missing (never committed; create it on the VM)"

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# --- Optional rollback tag --------------------------------------------------
if [[ "$DO_TAG" -eq 1 ]]; then
	STAMP="$(date +%Y%m%d-%H%M%S)"
	for img in backend frontend; do
		src="vanguard-md/${img}:prod"
		if docker image inspect "$src" >/dev/null 2>&1; then
			log "Tagging $src -> vanguard-md/${img}:prod-${STAMP} (rollback point)"
			docker tag "$src" "vanguard-md/${img}:prod-${STAMP}"
		fi
	done
fi

# --- Pull latest code -------------------------------------------------------
if [[ "$DO_PULL" -eq 1 ]]; then
	log "Fetching + fast-forwarding '$BRANCH'"
	git fetch --prune origin
	git checkout "$BRANCH"
	git pull --ff-only origin "$BRANCH"
	log "Now at $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"
else
	log "Skipping git pull (--no-pull); deploying $(git rev-parse --short HEAD)"
fi

# --- Bring the stack up -----------------------------------------------------
# NEXT_PUBLIC_* are compose build-args, so they must be in the shell env at
# build time. Sourcing the dashboard env makes them available for interpolation.
if [[ "$DO_BUILD" -eq 1 ]]; then
	log "Loading dashboard build-args from $DASHBOARD_ENV"
	set -a; . "./$DASHBOARD_ENV"; set +a
	log "Building + starting ${SERVICE:-all services}"
	compose up -d --build --wait --wait-timeout "$COMPOSE_WAIT_TIMEOUT" ${SERVICE:+$SERVICE}
else
	log "Recreating ${SERVICE:-all services} without rebuild (env-only change)"
	compose up -d --force-recreate --wait --wait-timeout "$COMPOSE_WAIT_TIMEOUT" ${SERVICE:+$SERVICE}
fi

# --- Verify -----------------------------------------------------------------
log "Container status"
compose ps

log "Waiting for backend health"
for i in {1..12}; do
	if docker exec "$BACKEND_CONTAINER" curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
		log "Backend healthy"
		break
	fi
	[[ "$i" -eq 12 ]] && die "Backend did not become healthy in time — check: compose logs --tail=100 backend"
	sleep 5
done

log "Public liveness check ($HEALTH_URL)"
curl -fsS "$HEALTH_URL" >/dev/null 2>&1 ||
	die "$HEALTH_URL is not reachable through DNS/TLS/edge"

log "Public readiness check ($READY_URL)"
curl -fsS "$READY_URL" >/dev/null 2>&1 ||
	die "$READY_URL reports that a required dependency is unavailable"

if [[ -z "$SERVICE" || "$SERVICE" == "frontend" || "$SERVICE" == "caddy" ]]; then
	log "Dashboard check ($DASHBOARD_URL)"
	curl -fsS "$DASHBOARD_URL" >/dev/null 2>&1 ||
		die "$DASHBOARD_URL is not serving the dashboard"
fi

log "Live site healthy ✔  Deploy complete."
