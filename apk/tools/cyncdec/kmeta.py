"""Recover original Kotlin names from the surviving `@Metadata` annotations.

R8 renamed the *fields* (`f34717r`) but left `kotlin.Metadata` intact, and its
`d2` array is a plain string table of the class's own declared names in source
order: the class descriptor, its supertypes, then property/function names.

So `SetBrightnessCommand$Companion` carrying `["OPCODE", "OPCODE_BYTES"]` tells
you the two constants in that companion were called OPCODE and OPCODE_BYTES -
which is how you know `f34717r = {-46, 17, 2}` is an opcode and not a colour.

Caveat, stated plainly: this reads the *name table*, not the `d1` protobuf that
binds names to signatures. Order is a strong hint, not a proof. Decoding `d1`
properly needs kotlinx-metadata-jvm against the original .dex; if a mapping
matters, confirm it against how the field is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# `m28776d2 = {"...", "..."}` - JADX prefixes the annotation members with mNNNNN.
_D2 = re.compile(r"d2\s*=\s*\{(.*?)\}\s*(?:,|\))", re.DOTALL)
_D1 = re.compile(r"d1\s*=\s*\{(.*?)\}\s*,", re.DOTALL)
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_CLASS_LINE = re.compile(
    r"^\s*(?:public|private|protected|final|static|abstract|synthetic|/\*.*?\*/|\s)*"
    r"(?:class|interface|enum)\s+(\w+)",
    re.MULTILINE,
)


def _unescape(s: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s).replace(
        "\\\\", "\\"
    ).replace('\\"', '"')


@dataclass
class MetaBlock:
    """One `@Metadata(...)` annotation's recovered name table."""

    owner: str  # the class the annotation sits on, best-effort
    descriptors: list[str]  # JVM type descriptors: Lcom/foo/Bar;
    names: list[str]  # everything else: property and function names, in order

    @property
    def kotlin_class(self) -> str:
        if not self.descriptors:
            return self.owner
        return self.descriptors[0].strip("L;").replace("/", ".")

    @property
    def supertypes(self) -> list[str]:
        return [d.strip("L;").replace("/", ".") for d in self.descriptors[1:]]


def parse(text: str) -> list[MetaBlock]:
    """Extract every @Metadata name table in a decompiled file, in file order."""
    blocks: list[MetaBlock] = []
    for m in _D2.finditer(text):
        raw = [_unescape(s) for s in _STRING.findall(m.group(1))]
        # The last entry is the module name ("ge-sdk_release"); drop it.
        if raw and raw[-1].endswith("_release"):
            raw = raw[:-1]
        descriptors = [s for s in raw if s.startswith("L") and s.endswith(";")]
        names = [
            s
            for s in raw
            if s not in descriptors and s and not s.startswith("(") and s != "<init>"
        ]
        # Attribute the block to the next class declaration after it.
        tail = text[m.end() :]
        owner_m = _CLASS_LINE.search(tail)
        blocks.append(
            MetaBlock(
                owner=owner_m.group(1) if owner_m else "?",
                descriptors=descriptors,
                names=names,
            )
        )
    return blocks


def summary(text: str) -> list[str]:
    """Human-readable lines describing what the metadata recovered."""
    out: list[str] = []
    for b in parse(text):
        head = f"{b.owner}: kotlin class {b.kotlin_class}"
        if b.supertypes:
            head += f" extends/implements {', '.join(b.supertypes)}"
        out.append(head)
        if b.names:
            out.append(f"    declared names (source order): {', '.join(b.names)}")
    return out


def strip_metadata(text: str) -> str:
    """Delete `@Metadata(...)` annotation lines - they are unreadable noise."""
    return "\n".join(
        l for l in text.splitlines() if not l.lstrip().startswith("@Metadata(")
    )
