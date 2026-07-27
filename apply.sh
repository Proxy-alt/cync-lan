#!/usr/bin/env bash
# Rebuild the annotated decompile of the Cync Android app.
#
# The decompiled tree is not distributed here - it is Savant's compiled code.
# This regenerates it from an APK you supply, then applies the annotations
# from this branch on top.
#
# Usage:  ./apply.sh /path/to/cync.apk [output-dir]
set -euo pipefail

APK="${1:-}"
OUT="${2:-./decompiled}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXPECTED_SHA="3d57c08b5c180bf7872c91186a78dc60e2684fb92796feb78ac9163bf33ec06c"
EXPECTED_FILES=55368

if [ -z "$APK" ] || [ ! -f "$APK" ]; then
  echo "usage: $0 /path/to/cync.apk [output-dir]" >&2
  exit 2
fi

command -v jadx >/dev/null || { echo "jadx not found on PATH" >&2; exit 3; }

echo "==> checking the APK"
actual_sha=$(shasum -a 256 "$APK" | cut -d' ' -f1)
if [ "$actual_sha" != "$EXPECTED_SHA" ]; then
  echo "    WARNING: sha256 does not match the APK these annotations were made against."
  echo "      expected $EXPECTED_SHA"
  echo "      actual   $actual_sha"
  echo "    Line numbers and generated names will differ; patches may not apply."
fi

echo "==> decompiling (this takes a while)"
# --comments-level debug is the load-bearing one: without it jadx silently
# drops the body of any method it fails to decompile, 254 files' worth.
# --deobf is easy to miss - it only renames identifiers shorter than 3 chars,
# so long class names look untouched either way.
jadx --comments-level debug --no-inline-anonymous --deobf -r -d "$OUT" "$APK"

echo "==> verifying the tree"
skipped=$(grep -rl "Method dump skipped" "$OUT/sources" 2>/dev/null | wc -l | tr -d ' ')
consts=$(grep -rl "Tnaf.POW_2_WIDTH" "$OUT/sources" 2>/dev/null | wc -l | tr -d ' ')
count=$(find "$OUT/sources" -name '*.java' | wc -l | tr -d ' ')
printf '    skipped method dumps: %s (want 0)\n' "$skipped"
printf '    const replacement:    %s files (want >0)\n' "$consts"
printf '    java files:           %s (want %s)\n' "$count" "$EXPECTED_FILES"
[ "$skipped" = "0" ] || echo "    WARNING: --comments-level debug did not take effect"

echo "==> applying annotations"
cd "$OUT"
# One hunk is expected to fail: C2184d.java is a jadx-generated filename and
# the counter in it is not stable between runs. See README.md.
patch -p1 --forward --batch -F3 < "$HERE/patches/0001-cync-lan-annotations.patch" || true

echo "==> done"
echo "    tree:  $OUT/sources"
echo "    tools: CYNCDEC_ROOT=$OUT $HERE/tools/cyncdec.sh map"
