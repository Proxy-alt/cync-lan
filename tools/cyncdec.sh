#!/usr/bin/env bash
# Run cyncdec from anywhere: `tools/cyncdec.sh map`, or via a symlink on PATH.
#
# Resolves symlinks before locating the package, so `ln -s .../tools/cyncdec.sh
# /usr/local/bin/cyncdec` works - otherwise PYTHONPATH would point at the
# symlink's directory rather than the real tools/.
set -euo pipefail

target="${BASH_SOURCE[0]}"
while [ -L "$target" ]; do
  link="$(readlink "$target")"
  case "$link" in
    /*) target="$link" ;;
    *)  target="$(cd "$(dirname "$target")" && pwd)/$link" ;;
  esac
done
here="$(cd "$(dirname "$target")" && pwd)"

exec env PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" python3 -m cyncdec "$@"
