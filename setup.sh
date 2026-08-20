#!/usr/bin/env sh
# Convenience wrapper: run the cross-platform setup with the system python.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$DIR/scripts/setup.py" "$@"
