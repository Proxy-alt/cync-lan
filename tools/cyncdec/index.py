"""A cached index of every class in the tree, plus scoped search over it.

Grepping 3 GB of decompile for a symbol is slow and mostly returns vendor hits.
The index lets every other tool resolve `SetBrightnessCommand` -> a path in one
dict lookup, and restrict full-text search to Cync's own packages.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import paths


@dataclass
class Index:
    root: Path
    src: Path
    # simple class name -> [fqn, ...] (a name can collide across packages)
    by_name: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    # fqn -> sources/-relative path
    by_fqn: dict[str, str] = field(default_factory=dict)

    def path(self, fqn: str) -> Path:
        return self.src / self.by_fqn[fqn]

    def resolve(self, query: str, app_only: bool = True) -> list[str]:
        """Resolve a class name, partial FQN, or file path to a list of FQNs."""
        q = query.replace("/", ".").removesuffix(".java")
        if q in self.by_fqn:
            return [q]
        hits: list[str] = []
        simple = q.rsplit(".", 1)[-1]
        for fqn in self.by_name.get(simple, []):
            if fqn.endswith(q) or fqn == q:
                hits.append(fqn)

        # A class jadx generated a name for (`C2184d`) may be cited either way,
        # and the counter half is not stable between decompiles - so accept the
        # stable name too. `cyncdec read d` finds `C2184d`.
        if not hits:
            for name, fqns in self.by_name.items():
                if paths.stable_name(name) == simple and name != simple:
                    hits.extend(fqns)
        if not hits:  # last resort: substring over all FQNs
            hits = [f for f in self.by_fqn if q.lower() in f.lower()]
        if app_only:
            app = [f for f in hits if paths.is_app(self.by_fqn[f])]
            if app:
                return sorted(app)
        return sorted(hits)

    def files(self, scope: str = "app") -> list[str]:
        """All sources/-relative paths in a scope.

        scope is one of `app` (Cync code + the SDKs it talks protocol to),
        `vendor`, `unknown`, `all`, or a literal path prefix such as
        `com/gelighting/cbygekit/services/devices`.
        """
        vals = self.by_fqn.values()
        if scope == "all":
            return sorted(vals)
        if scope == "app":
            return sorted(p for p in vals if paths.is_app(p))
        if scope in ("vendor", "near", "unknown"):
            return sorted(p for p in vals if paths.classify(p) == scope)
        prefix = scope.replace(".", "/").rstrip("/") + "/"
        return sorted(p for p in vals if p.startswith(prefix))


_CACHE_VERSION = 2


def build(root: Path | None = None, *, refresh: bool = False, quiet: bool = True) -> Index:
    root = root or paths.find_root()
    src = paths.sources(root)
    cache = root / "tools" / ".cache" / "index.json"

    if cache.exists() and not refresh:
        try:
            blob = json.loads(cache.read_text())
            if blob.get("version") == _CACHE_VERSION:
                idx = Index(root=root, src=src, by_fqn=blob["by_fqn"])
                for fqn in idx.by_fqn:
                    idx.by_name[fqn.rsplit(".", 1)[-1]].append(fqn)
                return idx
        except (json.JSONDecodeError, KeyError):
            pass  # rebuild

    t0 = time.time()
    idx = Index(root=root, src=src)
    for p in src.rglob("*.java"):
        rel = str(p.relative_to(src))
        fqn = rel.removesuffix(".java").replace("/", ".")
        idx.by_fqn[fqn] = rel
        idx.by_name[fqn.rsplit(".", 1)[-1]].append(fqn)

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"version": _CACHE_VERSION, "by_fqn": idx.by_fqn}))
    if not quiet:
        print(f"indexed {len(idx.by_fqn)} classes in {time.time() - t0:.1f}s -> {cache}")
    return idx


# --- full-text search --------------------------------------------------------

# JADX writes every inner/anonymous class to its own file. When walking a class
# graph these are usually noise, so they can be folded into their outer class.
_INNER = re.compile(r"\$")


def is_inner(fqn: str) -> bool:
    return "$" in fqn.rsplit(".", 1)[-1]


def outer(fqn: str) -> str:
    pkg, _, simple = fqn.rpartition(".")
    return f"{pkg}.{simple.split('$', 1)[0]}" if pkg else simple.split("$", 1)[0]


def grep(
    idx: Index,
    pattern: str,
    *,
    scope: str = "app",
    regex: bool = False,
    ignore_case: bool = False,
    limit: int = 0,
) -> list[tuple[str, int, str]]:
    """Search file contents. Returns (sources-relative path, lineno, line)."""
    flags = re.IGNORECASE if ignore_case else 0
    rx = re.compile(pattern if regex else re.escape(pattern), flags)
    out: list[tuple[str, int, str]] = []
    for rel in idx.files(scope):
        try:
            text = (idx.src / rel).read_text(errors="replace")
        except OSError:
            continue
        if not rx.search(text):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                out.append((rel, n, line.rstrip()))
                if limit and len(out) >= limit:
                    return out
    return out
