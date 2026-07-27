#!/usr/bin/env bash
# Run cyncdec from anywhere: `tools/cyncdec.sh map`, or via a symlink on PATH.
#
# Resolves symlinks before locating the package, so `ln -s .../tools/cyncdec.sh
# /usr/local/bin/cyncdec` works - otherwise PYTHONPATH would point at the
# symlink's directory rather than the real tools/.
set -euo pipefail

# Where we were *invoked* from, before following any symlinks. When tools/ is a
# symlink into a checkout of this branch, this is the decompile tree and the
# resolved path is not - so autodetection has to use this one. Without it,
# `tools/cyncdec.sh map` inside a decompile fails to find sources/.
invoked_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

target="${BASH_SOURCE[0]}"
while [ -L "$target" ]; do
  link="$(readlink "$target")"
  case "$link" in
    /*) target="$link" ;;
    *)  target="$(cd "$(dirname "$target")" && pwd)/$link" ;;
  esac
done
here="$(cd "$(dirname "$target")" && pwd)"

# An explicit CYNCDEC_ROOT always wins; otherwise, if we were invoked from
# inside a decompile tree, use that.
if [ -z "${CYNCDEC_ROOT:-}" ]; then
  probe="$invoked_dir"
  while [ "$probe" != "/" ]; do
    if [ -d "$probe/sources" ]; then
      export CYNCDEC_ROOT="$probe"
      break
    fi
    probe="$(dirname "$probe")"
  done
fi

exec env PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" python3 -m cyncdec "$@"
