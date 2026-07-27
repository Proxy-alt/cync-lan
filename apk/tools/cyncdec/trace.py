"""Cross-references and process extraction.

The question you actually ask of a decompiled app is never "what does this class
do" - it is "what happens, end to end, when the user taps the brightness
slider". Answering that by hand means grepping a class name, opening six files,
grepping the next name, and losing your place.

`callers()` and `callees()` do one hop of that. `walk()` does all of them and
prints the tree. `process()` renders the whole thing as a markdown report with
the opcode bytes and enum constants already resolved, which is the form the
findings actually need to be written up in.

Edges are derived from imports plus same-package textual references. That is a
*reference* graph, not a true call graph - it will include a class that is only
named in a type signature. It is deliberately over-inclusive: for RE work a
missing edge costs far more than an extra one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import enums, hexify, index, paths

_IMPORT = re.compile(r"^import\s+([\w.]+);", re.MULTILINE)
_WORD = re.compile(r"\b[A-Z]\w{2,}\b")

# Hilt/Dagger emits a factory, an injector and a component entry for every
# single injectable class. Walking up from anything reachable by DI otherwise
# drowns in them, and they carry no behaviour worth reading.
DEFAULT_SKIP = (
    "Dagger",
    "_Factory",
    "Hilt_",
    "_MembersInjector",
    "_GeneratedInjector",
    "_HiltModules",
    "_Impl",
    "$$",
    "databinding.",
)


@dataclass
class Node:
    fqn: str
    rel: str
    depth: int
    via: str | None = None  # the parent that reached it


def _text(idx: index.Index, fqn: str) -> str:
    rel = idx.by_fqn.get(fqn)
    if not rel:
        return ""
    try:
        return (idx.src / rel).read_text(errors="replace")
    except OSError:
        return ""


def callees(idx: index.Index, fqn: str, *, app_only: bool = True) -> list[str]:
    """Classes this class references (imports + same-package names)."""
    text = _text(idx, fqn)
    if not text:
        return []
    out: set[str] = set()

    for imported in _IMPORT.findall(text):
        if imported in idx.by_fqn and (not app_only or paths.is_app(idx.by_fqn[imported])):
            out.add(imported)

    pkg = fqn.rsplit(".", 1)[0]
    body = text[text.find("{") :] if "{" in text else text
    for word in set(_WORD.findall(body)):
        cand = f"{pkg}.{word}"
        if cand != fqn and cand in idx.by_fqn:
            if not app_only or paths.is_app(idx.by_fqn[cand]):
                out.add(cand)

    out.discard(fqn)
    return sorted(out)


def callers(idx: index.Index, fqn: str, *, scope: str = "app", fold_inner: bool = True) -> list[str]:
    """Classes that reference this one."""
    simple = fqn.rsplit(".", 1)[-1]
    needle_import = f"import {fqn};"
    rx = re.compile(rf"\b{re.escape(simple)}\b")
    hits: set[str] = set()
    for rel in idx.files(scope):
        other = rel.removesuffix(".java").replace("/", ".")
        if other == fqn:
            continue
        try:
            text = (idx.src / rel).read_text(errors="replace")
        except OSError:
            continue
        if needle_import in text or (
            other.rsplit(".", 1)[0] == fqn.rsplit(".", 1)[0] and rx.search(text)
        ):
            hits.add(index.outer(other) if fold_inner else other)
    hits.discard(fqn)
    return sorted(hits)


def walk(
    idx: index.Index,
    start: str,
    *,
    depth: int = 2,
    direction: str = "down",
    app_only: bool = True,
    limit: int = 400,
    skip: tuple[str, ...] | None = None,
) -> list[Node]:
    """Breadth-first walk of the reference graph from `start`.

    `skip` defaults to DEFAULT_SKIP (generated DI/databinding classes); pass an
    empty tuple to walk everything.
    """
    skip = DEFAULT_SKIP if skip is None else skip
    seen = {start}
    queue = [Node(start, idx.by_fqn.get(start, "?"), 0)]
    out: list[Node] = []
    i = 0
    while i < len(queue) and len(out) < limit:
        node = queue[i]
        i += 1
        out.append(node)
        if node.depth >= depth:
            continue
        nxt = (
            callees(idx, node.fqn, app_only=app_only)
            if direction == "down"
            else callers(idx, node.fqn)
        )
        for child in nxt:
            if child in seen or any(s in child for s in skip):
                continue
            seen.add(child)
            queue.append(Node(child, idx.by_fqn.get(child, "?"), node.depth + 1, node.fqn))
    return out


def _short_pkg(fqn: str) -> str:
    return (
        fqn.rsplit(".", 1)[0]
        .replace("com.gelighting.cbygekit.", "~.")
        .replace("com.savantsystems.oneapp", "~app")
    )


def tree(nodes: list[Node]) -> str:
    """Render a walk as an indented tree, following the edges that found it.

    The walk is breadth-first, so indenting purely by depth would show every
    depth-2 node hanging off whichever depth-1 node happened to come last.
    Each node records the parent that reached it; this groups by that.
    """
    children: dict[str | None, list[Node]] = {}
    for n in nodes:
        children.setdefault(n.via, []).append(n)

    lines: list[str] = []

    def emit(node: Node, prefix: str, last: bool, root: bool = False) -> None:
        simple = node.fqn.rsplit(".", 1)[-1]
        if root:
            lines.append(f"{simple}   ({_short_pkg(node.fqn)})")
            child_prefix = ""
        else:
            lines.append(f"{prefix}{'`- ' if last else '|- '}{simple}   ({_short_pkg(node.fqn)})")
            child_prefix = prefix + ("   " if last else "|  ")
        kids = children.get(node.fqn, [])
        for i, kid in enumerate(kids):
            emit(kid, child_prefix, i == len(kids) - 1)

    roots = [n for n in nodes if n.via is None]
    for r in roots:
        emit(r, "", True, root=True)
    return "\n".join(lines)


# --- the interesting bit: a written-up process report ------------------------

_SIGNATURE = re.compile(
    r"^\s{4}(?:public|protected)\s+(?:static\s+|final\s+|synchronized\s+)*"
    r"([\w.<>\[\], ?]+?)\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)
_BYTES = re.compile(r"new byte\[\]\s*\{([-\d,\s]+)\}")


@dataclass
class ClassCard:
    fqn: str
    rel: str
    depth: int
    kotlin_names: list[str] = field(default_factory=list)
    byte_literals: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    enum_refs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def card(idx: index.Index, node: Node, emap: dict[str, str]) -> ClassCard:
    from . import kmeta

    text = _text(idx, node.fqn)
    c = ClassCard(fqn=node.fqn, rel=node.rel, depth=node.depth)

    for b in kmeta.parse(text):
        c.kotlin_names.extend(b.names)

    for m in _BYTES.finditer(text):
        nums = [int(x) for x in m.group(1).split(",")]
        c.byte_literals.append(" ".join(f"{n & 0xFF:02X}" for n in nums))

    for m in _SIGNATURE.finditer(text):
        ret, name, params = m.groups()
        if name in ("toString", "hashCode", "equals"):
            continue
        short = re.sub(r"[\w.]+\.", "", params)
        c.methods.append(f"{name}({short}) -> {ret.split('.')[-1]}")

    for m in enums._REF.finditer(text):
        resolved = emap.get(f"{m.group(1)}.{m.group(2)}")
        if resolved:
            c.enum_refs.append(f"{m.group(1)}.{resolved}")

    c.notes = [
        l.strip().strip("/*").strip()
        for l in text.splitlines()
        if "cync-lan reverse-engineering note" in l.lower()
    ]
    c.enum_refs = sorted(dict.fromkeys(c.enum_refs))
    c.byte_literals = list(dict.fromkeys(c.byte_literals))
    return c


def process(
    idx: index.Index,
    start: str,
    *,
    depth: int = 2,
    limit: int = 40,
    emap: dict[str, str] | None = None,
) -> str:
    """Markdown write-up of everything reachable from `start`."""
    emap = emap if emap is not None else enums.build_map(idx)
    nodes = walk(idx, start, depth=depth, limit=limit)
    cards = [card(idx, n, emap) for n in nodes]

    head = start.rsplit(".", 1)[-1]
    out = [
        f"# Process: {head}",
        "",
        f"Reference graph from `{start}`, depth {depth}, {len(cards)} classes.",
        "Generated by `tools/cyncdec process` - edges are references, not proven calls.",
        "",
        "## Call tree",
        "",
        "```",
        tree(nodes),
        "```",
        "",
        "## Classes",
        "",
    ]
    for c in cards:
        out.append(f"### `{c.fqn.rsplit('.', 1)[-1]}`")
        out.append("")
        out.append(f"`{c.rel}`")
        out.append("")
        if c.byte_literals:
            out.append(f"- **byte literals**: {', '.join('`' + b + '`' for b in c.byte_literals)}")
        if c.kotlin_names:
            out.append(f"- **Kotlin names**: {', '.join(c.kotlin_names[:20])}")
        if c.enum_refs:
            out.append(f"- **enum constants used**: {', '.join(c.enum_refs[:20])}")
        if c.methods:
            out.append("- **methods**:")
            for m in c.methods[:15]:
                out.append(f"    - `{m}`")
        for n in c.notes:
            out.append(f"- **existing note**: {n}")
        out.append("")
    return "\n".join(out)
