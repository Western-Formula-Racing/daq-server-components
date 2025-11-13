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
  cp "$ENV_TEMPLATE" "$ENV_FILE"
elif [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_TEMPLATE" "$ENV_FILE"
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

  compose ps || true
  if [[ $exit_code -ne 0 ]]; then
    compose logs --tail 200 || true
  fi

  if [[ "${KEEP_DAQ_STACK:-0}" != "1" ]]; then
    compose down -v --remove-orphans || true
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

compose up --detach --build --remove-orphans "${ENABLED_SERVICES[@]}"

inspect_container() {
  local name="$1"
  docker ps -a --filter "name=${name}" \
    --format '{{.ID}} {{.State.Status}} {{.State.ExitCode}}' \
    | head -n 1
}

ready_timeout_seconds=$((SECONDS + 600))

while (( SECONDS < ready_timeout_seconds )); do
  not_ready=()
  ready_summary=()

  for service in "${ENABLED_SERVICES[@]}"; do
    container_info="$(inspect_container "$service")"

    if [[ -z "$container_info" ]]; then
      not_ready+=("$service(no-container-yet)")
      continue
    fi

    container_id=$(echo "$container_info" | awk '{print $1}')
    status=$(echo "$container_info" | awk '{print $2}')
    code=$(echo "$container_info" | awk '{print $3}')

    if [[ "$service" == "startup-data-loader" ]]; then
      if [[ "$status" == "exited" && "$code" -eq 0 ]]; then
        ready_summary+=("$service=exited(0)")
      else
        not_ready+=("$service=$status/$code")
      fi
      continue
    fi

    if [[ "$status" != "running" ]]; then
      not_ready+=("$service=$status")
      continue
    fi

    has_health="$(docker inspect -f '{{if .State.Health}}true{{else}}false{{end}}' "$container_id")"
    health_status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"

    if [[ "$has_health" == "true" && "$health_status" != "healthy" ]]; then
      not_ready+=("$service=health:$health_status")
      continue
    fi

    if [[ "$has_health" == "true" ]]; then
      ready_summary+=("$service=running/$health_status")
    else
      ready_summary+=("$service=running")
    fi
  done

  if [[ ${#not_ready[@]} -eq 0 ]]; then
    echo "All services ready: ${ready_summary[*]}"
    exit 0
  fi

  echo "Waiting for services: ${not_ready[*]}"
  sleep 10
done

echo "Timed out waiting for services to become ready." >&2
exit 1