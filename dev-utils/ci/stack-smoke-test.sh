#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALLER_DIR="$REPO_ROOT/installer"
ENV_TEMPLATE="$INSTALLER_DIR/.env.example"
ENV_FILE="$INSTALLER_DIR/.env"

if [[ ! -d "$INSTALLER_DIR" ]]; then
  echo "Installer directory not found at $INSTALLER_DIR" >&2
  exit 1
fi

if [[ ! -f "$ENV_TEMPLATE" ]]; then
  echo "Missing environment template at $ENV_TEMPLATE" >&2
  exit 1
fi

if [[ "${CI:-}" == "true" ]]; then
  echo "[ci] Copying $ENV_TEMPLATE -> $ENV_FILE"
  cp "$ENV_TEMPLATE" "$ENV_FILE"
elif [[ ! -f "$ENV_FILE" ]]; then
  echo "Local .env not found; copying template for convenience."
  cp "$ENV_TEMPLATE" "$ENV_FILE"
else
  echo "Using existing $ENV_FILE"
fi

pushd "$INSTALLER_DIR" >/dev/null

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-daqstackci}"

compose() {
  docker compose "$@"
}

cleanup() {
  local exit_code=$1
  trap - EXIT
  set +e
  echo "----- docker compose ps -----"
  compose ps || true
  if [[ $exit_code -ne 0 ]]; then
    echo "----- docker compose logs (tail) -----"
    compose logs --tail 200 || true
  fi
  if [[ "${KEEP_DAQ_STACK:-0}" != "1" ]]; then
    compose down -v --remove-orphans || true
  else
    echo "KEEP_DAQ_STACK=1 set; containers left running for inspection."
  fi
  popd >/dev/null || true
  exit "$exit_code"
}
trap 'cleanup $?' EXIT

ENABLED_SERVICES=(
  influxdb3
  influxdb3-explorer
  telegraf
  grafana
  frontend
  lap-detector
  startup-data-loader
  file-uploader
)

echo "Launching stack services: ${ENABLED_SERVICES[*]}"
compose up --detach --build --remove-orphans "${ENABLED_SERVICES[@]}"

ready_timeout_seconds=$((SECONDS + 600))

while (( SECONDS < ready_timeout_seconds )); do
  not_ready=()
  ready_summary=()
  for service in "${ENABLED_SERVICES[@]}"; do
    container_id="$(compose ps -q "$service")"
    if [[ -z "$container_id" ]]; then
      not_ready+=("$service(no-container)")
      continue
    fi

    state_status="$(docker inspect -f '{{.State.Status}}' "$container_id")"
    health_required="$(docker inspect -f '{{if .State.Health}}true{{else}}false{{end}}' "$container_id")"
    health_status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
    exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$container_id")"

    if [[ "$service" == "startup-data-loader" ]]; then
      if [[ "$state_status" == "exited" && "$exit_code" -eq 0 ]]; then
        ready_summary+=("$service=exited(0)")
      else
        not_ready+=("$service=${state_status}/exit:${exit_code}")
      fi
      continue
    fi

    if [[ "$state_status" != "running" ]]; then
      not_ready+=("$service=$state_status")
      continue
    fi

    if [[ "$health_required" == "true" && "$health_status" != "healthy" ]]; then
      not_ready+=("$service=health:$health_status")
      continue
    fi

    if [[ "$health_required" == "true" ]]; then
      ready_summary+=("$service=running/$health_status")
    else
      ready_summary+=("$service=running")
    fi
  done

  if [[ ${#not_ready[@]} -eq 0 ]]; then
    echo "All services ready: ${ready_summary[*]}"
    echo "Stack is healthy; proceeding to teardown."
    exit 0
  fi

  echo "Waiting for services: ${not_ready[*]}"
  sleep 10
done

echo "Timed out waiting for services to become ready." >&2
exit 1
