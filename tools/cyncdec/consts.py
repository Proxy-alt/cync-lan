"""Resolve symbolic constants that R8's constant-merging left in byte arrays.

R8 dedupes identical compile-time constants across the whole APK, so a literal
`16` in a Cync opcode array can end up pointing at whichever class happened to
win the merge - frequently a bundled vendor library. JADX then faithfully emits
the symbol:

    f34542w = new byte[]{-57, 17, 2, Tnaf.POW_2_WIDTH, 0};

`Tnaf` is BouncyCastle elliptic-curve code with no relationship to lighting
whatsoever; `Tnaf.POW_2_WIDTH` is just `16`. The array means `C7 11 02 10 00`.

This mattered: it hid 16 query-command opcodes from the extractor, because a
single symbolic element made the whole initialiser fail an all-numeric match.

Resolution is lazy and per-symbol - one class file read, then cached. Scanning
the whole tree up front would cost ~80s for a handful of lookups.
"""

from __future__ import annotations

import re

from . import index

# `public static final byte POW_2_WIDTH = 16;` - also int/short, and hex forms.
_CONST_DECL = r"static\s+final\s+(?:byte|int|short|char)\s+{name}\s*=\s*(-?(?:0[xX][0-9a-fA-F]+|\d+))"


class ConstResolver:
    """Looks up `Class.CONSTANT` -> int, reading at most one file per class."""

    def __init__(self, idx: index.Index) -> None:
        self.idx = idx
        self._cache: dict[str, int | None] = {}

    def resolve(self, ref: str) -> int | None:
        """`Tnaf.POW_2_WIDTH` or a bare `POW_2_WIDTH` -> 16, or None."""
        if ref in self._cache:
            return self._cache[ref]
        self._cache[ref] = value = self._lookup(ref)
        return value

    def _lookup(self, ref: str) -> int | None:
        cls, _, name = ref.rpartition(".")
        if not name.isidentifier():
            return None

        if cls:
            candidates = self.idx.by_name.get(cls.rsplit(".", 1)[-1], [])
        else:
            # No qualifier: the constant is declared in the file itself, which
            # the caller handles. Nothing to look up tree-wide.
            return None

        rx = re.compile(_CONST_DECL.format(name=re.escape(name)))
        for fqn in candidates:
            try:
                text = (self.idx.src / self.idx.by_fqn[fqn]).read_text(errors="replace")
            except (OSError, KeyError):
                continue
            m = rx.search(text)
            if m:
                raw = m.group(1)
                return int(raw, 16) if raw.lower().startswith(("0x", "-0x")) else int(raw)
        return None


# Array elements: a signed number, or a (possibly qualified) identifier.
_ELEMENT = re.compile(r"(-?(?:0[xX][0-9a-fA-F]+|\d+))|([A-Za-z_][\w.]*)")


def parse_byte_array(
    body: str, resolver: ConstResolver | None = None, local: dict[str, int] | None = None
) -> list[int] | None:
    """Parse a `new byte[]{...}` initialiser body into ints.

    Returns None if any element cannot be resolved - a partially-resolved
    opcode is worse than no opcode, since it looks authoritative.
    """
    out: list[int] = []
    for m in _ELEMENT.finditer(body):
        num, sym = m.group(1), m.group(2)
        if num is not None:
            out.append(int(num, 16) if num.lower().startswith(("0x", "-0x")) else int(num))
            continue
        if local and sym in local:
            out.append(local[sym])
            continue
        value = resolver.resolve(sym) if resolver else None
        if value is None:
            return None
        out.append(value)
    return out or None


def local_constants(text: str) -> dict[str, int]:
    """`static final byte FOO = 3;` declared in this file."""
    rx = re.compile(
        r"static\s+final\s+(?:byte|int|short|char)\s+(\w+)\s*=\s*(-?(?:0[xX][0-9a-fA-F]+|\d+))"
    )
    out: dict[str, int] = {}
    for m in rx.finditer(text):
        raw = m.group(2)
        out[m.group(1)] = int(raw, 16) if raw.lower().startswith(("0x", "-0x")) else int(raw)
    return out
