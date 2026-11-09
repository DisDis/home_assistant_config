#!/usr/bin/env bash
# Interactive backup entry point; implementation uses Python's standard library.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 -B "$script_dir/backup.py" "$@"
