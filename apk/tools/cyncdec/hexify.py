"""Render Java's signed-decimal byte literals as hex.

JADX prints `new byte[]{-46, 17, 2}` where the protocol actually says
`D2 11 02`, and `(byte) -46` where the wire says `0xD2`. Every opcode in this
app is therefore invisible until you do the two's-complement conversion in your
head. This module does it for you.

Deliberately annotation-only by default: the hex is added as a trailing comment
so the file still reads as the decompiler emitted it, and nothing downstream
(diffing against a fresh decompile, `git blame` on the annotated tree) breaks.
"""

from __future__ import annotations

import re

__all__ = ["to_hex", "annotate_line", "annotate", "inline"]


def to_hex(v: int) -> str:
    """Signed Java byte (or any int) -> 0xNN."""
    return f"0x{v & 0xFF:02X}" if -128 <= v <= 255 else f"0x{v & 0xFFFFFFFF:X}"


# `new byte[]{...}` / `{-46, 17, 2}` array initialisers of numeric literals only.
_ARRAY = re.compile(r"\{\s*(-?\d+\s*(?:,\s*-?\d+\s*)*),?\s*\}")
# a `(byte) -46` cast
_CAST = re.compile(r"\(byte\)\s*(-?\d+)")


def _fmt_array(nums: list[int]) -> str:
    return " ".join(f"{n & 0xFF:02X}" for n in nums)


def annotate_line(line: str) -> str:
    """Append a `// hex: ...` comment to a line containing byte literals.

    Returns the line unchanged when there is nothing to say, or when the line
    already carries a hex annotation (so re-running is idempotent).
    """
    if "hex:" in line or line.lstrip().startswith(("//", "*", "/*")):
        return line

    notes: list[str] = []

    for m in _ARRAY.finditer(line):
        nums = [int(x) for x in m.group(1).split(",")]
        # Only worth annotating byte-ish arrays; a `{1, 2, 3}` of ints is noise.
        if len(nums) >= 2 and all(-128 <= n <= 255 for n in nums) and any(n < 0 or n > 9 for n in nums):
            notes.append("{" + _fmt_array(nums) + "}")

    casts = [int(m.group(1)) for m in _CAST.finditer(line)]
    notes.extend(to_hex(c) for c in casts)

    return f"{line}  // hex: {' '.join(notes)}" if notes else line


def annotate(text: str) -> str:
    return "\n".join(annotate_line(l) for l in text.splitlines())


def inline(text: str) -> str:
    """Rewrite the literals themselves to hex, rather than annotating.

    Produces code that no longer matches the decompiler output byte for byte -
    use for reading, not for a tree you intend to diff.
    """
    def arr(m: re.Match[str]) -> str:
        nums = [int(x) for x in m.group(1).split(",")]
        if not all(-128 <= n <= 255 for n in nums):
            return m.group(0)
        return "{" + ", ".join(to_hex(n) for n in nums) + "}"

    text = _ARRAY.sub(arr, text)
    return _CAST.sub(lambda m: f"(byte) {to_hex(int(m.group(1)))}", text)
