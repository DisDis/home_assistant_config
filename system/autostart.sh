#!/usr/bin/env bash
# Configure Docker autostart and start enabled Compose projects, or only check them.
set -euo pipefail

check_only=false
if [[ ${1:-} == --check ]]; then
  check_only=true
  shift
fi
if [[ $# -gt 3 || ${1:-} == --help ]]; then
  printf 'Usage: %s [--check] [HOMEASSISTANT_DIR [TOOLS_DIR [ADDONS_DIR]]]\n' "$0"
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $# -ge 1 ]]; then
  main_dir=$1
elif [[ -f "$(dirname -- "$script_dir")/homeassistant/docker-compose.yml" ]]; then
  # Repository layout: system/ and homeassistant/ are sibling directories.
  main_dir=$(dirname -- "$script_dir")/homeassistant
else
  # Deployed layout: system/ is inside the main Compose directory.
  main_dir=$(dirname -- "$script_dir")
fi
main_dir=$(cd -- "$main_dir" && pwd)
if [[ $# -ge 2 ]]; then
  tools_dir=$2
elif [[ -d "$main_dir/tools/portainer" ]]; then
  tools_dir=$main_dir/tools
else
  tools_dir=$(dirname -- "$main_dir")/tools
fi
tools_dir=$(cd -- "$tools_dir" && pwd)
if [[ $# -ge 3 ]]; then
  addons_dir=$3
else
  addons_dir=$(dirname -- "$main_dir")/addons
fi
addons_dir=$(cd -- "$addons_dir" && pwd)
projects=("$tools_dir/portainer")
if [[ -f "$tools_dir/uptime-kuma/docker-compose.yml" ]]; then
  projects+=("$tools_dir/uptime-kuma")
fi

compose() {
  local project_dir=$1
  shift
  local args=(--project-directory "$project_dir" -f "$project_dir/docker-compose.yml")
  if [[ -f "$project_dir/.env" ]]; then
    args+=(--env-file "$project_dir/.env")
  elif [[ "$project_dir" != "$main_dir" && -f "$tools_dir/.env" ]]; then
    # Existing installations can keep a shared tools/.env.
    args+=(--env-file "$tools_dir/.env")
  else
    args+=(--env-file /dev/null)
  fi
  docker compose "${args[@]}" "$@"
}

# Validate every template before changing services or containers.
docker compose version >/dev/null
for project_dir in "$main_dir" "${projects[@]}"; do
  compose "$project_dir" config --quiet
done
bash "$script_dir/addons.sh" validate "$addons_dir"

if ! "$check_only"; then
  for unit in containerd.service docker.service; do
    if ! systemctl is-enabled --quiet "$unit"; then
      sudo systemctl enable "$unit"
    fi
    if ! systemctl is-active --quiet "$unit"; then
      sudo systemctl start "$unit"
    fi
  done
fi

for unit in containerd.service docker.service; do
  systemctl is-enabled --quiet "$unit"
  systemctl is-active --quiet "$unit"
  printf '%s: enabled, active\n' "$unit"
done

docker info >/dev/null

if "$check_only"; then
  bash "$script_dir/ha.sh" check "$main_dir" "$addons_dir"
else
  bash "$script_dir/ha.sh" start "$main_dir" "$addons_dir"
fi

for project_dir in "${projects[@]}"; do
  services=$(compose "$project_dir" config --services)
  if [[ -z "$services" ]]; then
    printf '%s: no enabled services, skipped\n' "$project_dir"
    continue
  fi
  if ! "$check_only"; then
    # Image updates are a separate, explicit maintenance step.
    compose "$project_dir" up -d --pull never
  fi
  while IFS= read -r service; do
    ids=$(compose "$project_dir" ps --all --quiet "$service")
    if [[ -z "$ids" ]]; then
      printf 'ERROR: %s: container is missing\n' "$service" >&2
      exit 1
    fi
    while IFS= read -r container_id; do
      state=$(docker inspect --format '{{.State.Status}}' "$container_id")
      policy=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$container_id")
      if [[ "$state" != running || ( "$policy" != unless-stopped && "$policy" != always ) ]]; then
        printf 'ERROR: %s: state=%s, restart=%s\n' "$service" "$state" "$policy" >&2
        exit 1
      fi
      printf '%s: running, restart=%s\n' "$service" "$policy"
    done <<< "$ids"
  done <<< "$services"
done
