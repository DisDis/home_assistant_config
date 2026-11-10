#!/usr/bin/env bash
# Start with missing images and refresh latest; other updates are explicit maintenance.
set -euo pipefail

usage() {
  printf 'Usage: %s {start|stop|restart|check} [HOMEASSISTANT_DIR [ADDONS_DIR]]\n' "$0"
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  usage
  exit 0
fi
if [[ $# -lt 1 || $# -gt 3 ]]; then
  usage >&2
  exit 2
fi
action=$1
shift
case "$action" in
  start|stop|restart|check) ;;
  *) usage >&2; exit 2 ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $# -ge 1 ]]; then
  main_dir=$1
elif [[ -f "$(dirname -- "$script_dir")/homeassistant/docker-compose.yml" ]]; then
  main_dir=$(dirname -- "$script_dir")/homeassistant
else
  main_dir=$(dirname -- "$script_dir")
fi
main_dir=$(cd -- "$main_dir" && pwd)
addons_dir=${2:-$(dirname -- "$main_dir")/addons}
addons_dir=$(cd -- "$addons_dir" && pwd)

main_compose=(docker compose --project-directory "$main_dir" -f "$main_dir/docker-compose.yml")
if [[ -f "$main_dir/.env" ]]; then
  main_compose+=(--env-file "$main_dir/.env")
else
  main_compose+=(--env-file /dev/null)
fi

# Reject invalid Compose configuration before stopping or starting anything.
"${main_compose[@]}" config --quiet
bash "$script_dir/addons.sh" validate "$addons_dir"

start_system() {
  "${main_compose[@]}" pull --policy missing
  "${main_compose[@]}" up -d --pull never --wait --wait-timeout 60
  bash "$script_dir/addons.sh" start "$addons_dir"
  printf 'HA and selected addons: started\n'
}

stop_system() {
  bash "$script_dir/addons.sh" stop "$addons_dir"
  "${main_compose[@]}" stop --timeout 60
  printf 'HA and addons: stopped\n'
}

check_system() {
  local services service ids container_id state policy
  services=$("${main_compose[@]}" config --services)
  while IFS= read -r service; do
    [[ -n "$service" ]] || continue
    ids=$("${main_compose[@]}" ps --all --quiet "$service")
    if [[ -z "$ids" ]]; then
      printf 'ERROR: %s: container is missing\n' "$service" >&2
      return 1
    fi
    while IFS= read -r container_id; do
      state=$(docker inspect --format '{{.State.Status}}' "$container_id")
      policy=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$container_id")
      if [[ "$state" != running || ( "$policy" != unless-stopped && "$policy" != always ) ]]; then
        printf 'ERROR: %s: state=%s, restart=%s\n' "$service" "$state" "$policy" >&2
        return 1
      fi
      printf '%s: running, restart=%s\n' "$service" "$policy"
    done <<< "$ids"
  done <<< "$services"
  bash "$script_dir/addons.sh" check "$addons_dir"
}

case "$action" in
  start) start_system ;;
  stop) stop_system ;;
  restart) stop_system; start_system ;;
  check) check_system ;;
esac
