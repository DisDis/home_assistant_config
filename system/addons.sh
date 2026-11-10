#!/usr/bin/env bash
# Keep a shell entry point alongside autostart.sh; logic uses Python's stdlib.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$script_dir/addons.py" "$@"
