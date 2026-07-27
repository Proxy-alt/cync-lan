"""Recover enum constant names, which R8 could not strip.

Kotlin/Java enums keep their constant name as a string argument to the synthetic
constructor, so a decompiled enum looks like:

    public static final WriteType f34481a;
    ...
    WriteType writeType = new WriteType("DEFAULT", 0);
    f34481a = writeType;

which means `DeviceCommand.WriteType.f34482b` at a call site is really
`NO_ACKNOWLEDGEMENT`. Unlike the @Metadata name table in `kmeta`, this is an
exact mapping, not an ordering hint - the field and the name are bound by an
assignment in the same static block.

Enum constants are how this app expresses connection type, priority, device
capability and power state, so resolving them turns a lot of unreadable call
sites into plain English.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import index

# `new Foo("NAME", 3)` - the ordinal argument distinguishes an enum constructor
# from an ordinary one taking a string.
_ENUM_CTOR = re.compile(r"(\w+)\s+(\w+)\s*=\s*new\s+(\w+)\s*\(\s*\"([^\"]+)\"\s*,\s*(\d+)")
# `f34481a = writeType;`
_FIELD_ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*(\w+)\s*;", re.MULTILINE)
# JADX sometimes folds both steps: `f34481a = new Foo("NAME", 0);`
_DIRECT = re.compile(r"(\w+)\s*=\s*new\s+(\w+)\s*\(\s*\"([^\"]+)\"\s*,\s*(\d+)")


def parse(text: str) -> dict[str, tuple[str, str, int]]:
    """field name -> (enum class, constant name, ordinal) for one file."""
    out: dict[str, tuple[str, str, int]] = {}

    locals_: dict[str, tuple[str, str, int]] = {}
    for m in _ENUM_CTOR.finditer(text):
        _decl_type, local, cls, name, ordinal = m.groups()
        locals_[local] = (cls, name, int(ordinal))

    for m in _FIELD_ASSIGN.finditer(text):
        field, rhs = m.groups()
        if rhs in locals_:
            out[field] = locals_[rhs]

    for m in _DIRECT.finditer(text):
        field, cls, name, ordinal = m.groups()
        if field.startswith("f") or field.isupper():
            out.setdefault(field, (cls, name, int(ordinal)))

    return out


_CACHE_VERSION = 1


def build_map(idx: index.Index, *, scope: str = "app", refresh: bool = False) -> dict[str, str]:
    """Tree-wide `EnumClass.fieldName` -> `CONSTANT_NAME`.

    Also registers the bare `fieldName` when it is unambiguous across the tree,
    so a call site can be resolved without knowing the receiver's type.
    """
    cache = idx.root / "tools" / ".cache" / f"enums-{scope.replace('/', '_')}.json"
    if cache.exists() and not refresh:
        try:
            blob = json.loads(cache.read_text())
            if blob.get("version") == _CACHE_VERSION:
                return blob["map"]
        except (json.JSONDecodeError, KeyError):
            pass

    qualified: dict[str, str] = {}
    bare: dict[str, set[str]] = {}
    for rel in idx.files(scope):
        try:
            text = (idx.src / rel).read_text(errors="replace")
        except OSError:
            continue
        if "new " not in text:
            continue
        for field, (cls, name, _ordinal) in parse(text).items():
            # `new EnumDescriptor("com.foo.Bar", 3)` is a descriptor, not an
            # enum constant - real constant names are plain identifiers.
            if not name.isidentifier():
                continue
            qualified[f"{cls}.{field}"] = name
            bare.setdefault(field, set()).add(name)

    merged = dict(qualified)
    for field, names in bare.items():
        if len(names) == 1:
            merged.setdefault(field, next(iter(names)))

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"version": _CACHE_VERSION, "map": merged}))
    return merged


_REF = re.compile(r"\b([A-Z]\w*)\.(f\d+[a-z]?)\b")
_BARE_REF = re.compile(r"(?<![.\w])(f\d{4,}[a-z]?)\b")


def annotate_line(line: str, emap: dict[str, str]) -> str:
    """Append `// Foo.f1 = BAR` for enum constants referenced on this line."""
    if "=" in line and "//" in line and " = " in line.split("//", 1)[1]:
        return line  # already annotated
    hits: list[str] = []
    for m in _REF.finditer(line):
        cls, field = m.groups()
        name = emap.get(f"{cls}.{field}")
        if name:
            hits.append(f"{cls}.{field} = {name}")
    if not hits:
        return line
    sep = " " if line.rstrip().endswith(("//", "*/")) else "  // "
    return f"{line}{sep}{'; '.join(dict.fromkeys(hits))}"


def resolve(ref: str, emap: dict[str, str]) -> str | None:
    """Resolve `WriteType.f34482b` or a bare `f34482b` to its constant name."""
    if ref in emap:
        return emap[ref]
    return emap.get(ref.rsplit(".", 1)[-1])
