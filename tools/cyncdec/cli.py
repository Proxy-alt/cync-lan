"""`cyncdec` command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import devices, enums, hexify, index, kmeta, opcodes, paths, render, services, trace


def _idx(args) -> index.Index:
    root = Path(args.root).resolve() if args.root else None
    return index.build(root, refresh=getattr(args, "refresh", False), quiet=not args.verbose)


def _resolve_one(idx: index.Index, query: str) -> str:
    hits = idx.resolve(query)
    if not hits:
        sys.exit(f"no class matching {query!r} (try `cyncdec find {query}`)")
    if len(hits) > 1:
        print(f"# {len(hits)} matches for {query!r}, using {hits[0]}", file=sys.stderr)
        for h in hits[1:6]:
            print(f"#   also: {h}", file=sys.stderr)
    return hits[0]


# --- commands ----------------------------------------------------------------


def cmd_map(args) -> None:
    idx = _idx(args)
    counts: dict[str, int] = {}
    for rel in idx.by_fqn.values():
        counts[rel.rsplit("/", 1)[0]] = counts.get(rel.rsplit("/", 1)[0], 0) + 1

    def total(prefix: str) -> int:
        return sum(n for d, n in counts.items() if d == prefix or d.startswith(prefix + "/"))

    print(f"{len(idx.by_fqn)} classes under {idx.src}\n")
    for title, roots in (
        ("APP - Cync's own code", paths.APP_ROOTS),
        ("NEAR - bundled SDKs that carry the protocol", paths.NEAR_APP_ROOTS),
    ):
        print(title)
        for prefix, desc in roots.items():
            n = total(prefix)
            if n:
                print(f"  {n:>6}  {prefix:<34} {desc}")
        print()

    vendor = sum(1 for r in idx.by_fqn.values() if paths.classify(r) == "vendor")
    print(f"VENDOR - skipped by default\n  {vendor:>6}  {len(paths.VENDOR_ROOTS)} known third-party roots\n")

    unknown: dict[str, int] = {}
    for r in idx.by_fqn.values():
        if paths.classify(r) == "unknown":
            key = "/".join(r.split("/")[:2])
            unknown[key] = unknown.get(key, 0) + 1
    if unknown:
        print("UNCLASSIFIED - not in paths.py, worth a look")
        for u, n in sorted(unknown.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>6}  {u}")
        print()

    if args.deep:
        print("APP breakdown by package")
        app = {d: n for d, n in counts.items() if paths.classify(d) == "app"}
        for d, n in sorted(app.items(), key=lambda kv: -kv[1])[: args.deep]:
            print(f"  {n:>6}  {d}")


def cmd_find(args) -> None:
    idx = _idx(args)
    hits = idx.resolve(args.query, app_only=not args.all)
    for h in hits[: args.limit]:
        print(f"{h}\n    sources/{idx.by_fqn[h]}")
    if not hits:
        print(f"no class matched {args.query!r}", file=sys.stderr)


def cmd_grep(args) -> None:
    idx = _idx(args)
    for rel, n, line in index.grep(
        idx,
        args.pattern,
        scope=args.scope,
        regex=args.regex,
        ignore_case=args.ignore_case,
        limit=args.limit,
    ):
        print(f"sources/{rel}:{n}: {line.strip()}")


def cmd_read(args) -> None:
    idx = _idx(args)
    p = Path(args.target)
    text = p.read_text(errors="replace") if p.is_file() else (idx.src / idx.by_fqn[_resolve_one(idx, args.target)]).read_text(errors="replace")

    out = render.render(
        text,
        hex_bytes=not args.no_hex,
        inline_hex=args.inline_hex,
        strip_noise=not args.raw,
        show_names=not args.no_names,
        keep_annotations=args.raw,
    )
    if not args.no_enums:
        emap = enums.build_map(idx)
        out = "\n".join(enums.annotate_line(l, emap) for l in out.splitlines())
    print(out)


def cmd_names(args) -> None:
    idx = _idx(args)
    fqn = _resolve_one(idx, args.target)
    text = (idx.src / idx.by_fqn[fqn]).read_text(errors="replace")
    print(f"# {fqn}")
    for line in kmeta.summary(text) or ["  (no @Metadata found)"]:
        print(line)
    ec = enums.parse(text)
    if ec:
        print("\n# enum constants (exact - recovered from constructor strings)")
        for fieldname, (cls, name, ordinal) in sorted(ec.items(), key=lambda kv: kv[1][2]):
            print(f"  {cls}.{fieldname} = {name}  (ordinal {ordinal})")


def cmd_enums(args) -> None:
    idx = _idx(args)
    emap = enums.build_map(idx, refresh=args.refresh)
    if args.query:
        q = args.query.lower()
        items = {k: v for k, v in emap.items() if q in k.lower() or q in v.lower()}
    else:
        items = emap
    for k, v in sorted(items.items()):
        if "." in k or args.all:
            print(f"{k} = {v}")
    print(f"\n# {len(items)} of {len(emap)} entries", file=sys.stderr)


def cmd_devices(args) -> None:
    idx = _idx(args)
    dlist = devices.extract_device_types(idx)
    q = (args.query or "").lower()
    cat = (args.category or "").lower()
    
    print(f"# Cync Hardware Device Types ({len(dlist)} found)\n")
    for d in dlist:
        if q and q not in d.name.lower():
            continue
        if cat and cat not in d.category.lower():
            continue
        caps = f" [{', '.join(d.capabilities)}]" if d.capabilities else ""
        val_str = f"val={d.value:<3}" if d.value is not None else "val=-  "
        print(f"  {val_str}  {d.category:<12} {d.name}{caps}")


def cmd_services(args) -> None:
    idx = _idx(args)
    slist = services.extract_services(idx)
    q = (args.query or "").lower()
    
    print(f"# Decompiled Cync Services & Handlers ({len(slist)} found)\n")
    for s in slist:
        if q and q not in s.name.lower() and q not in s.category.lower():
            continue
        print(f"## {s.name} ({s.category})")
        print(f"   FQN: {s.fqn}")
        if s.commands_referenced:
            print(f"   Commands: {', '.join(s.commands_referenced[:8])}")
            if len(s.commands_referenced) > 8:
                print(f"   ... and {len(s.commands_referenced) - 8} more")
        print()


def cmd_opcodes(args) -> None:
    idx = _idx(args)
    cmds = opcodes.extract_all(idx)
    if getattr(args, "family", None):
        fam = args.family.lower()
        cmds = [c for c in cmds if fam in c.name.lower()]
    if args.json:
        out = opcodes.to_json(cmds)
    elif args.table:
        out = "\n".join(
            f"{c.telink_opcode or '-':<17} {c.xlink_opcode or '-':<17} "
            f"{c.xlink_outer_opcode or '-':<6} {c.name}"
            for c in sorted(cmds, key=lambda c: (c.opcode_bytes or "zz", c.name))
        )
    else:
        out = opcodes.to_markdown(cmds)
    if args.output:
        Path(args.output).write_text(out + "\n")
        print(f"wrote {args.output} ({len(cmds)} commands)", file=sys.stderr)
    else:
        print(out)


def cmd_codes(args) -> None:
    idx = _idx(args)
    from . import consts

    table = opcodes.command_code_table(idx, consts.ConstResolver(idx))
    if not table:
        sys.exit("XlinkCommandCode not found in this tree")
    used: dict[str, list[str]] = {}
    if args.usage:
        for c in opcodes.extract_all(idx):
            # A code maps to a command via its scalar op, its runtime variants,
            # or - for mesh commands - the outer op the Xlink path wraps it in.
            # Omitting that last one would report SET_POWER_STATE as having no
            # implementation while SetPowerStateCommand sits right there.
            for key in filter(None, [c.scalar_op, c.xlink_outer_opcode]):
                used.setdefault(key, []).append(c.name)
            for v in c.op_variants.values():
                used.setdefault(v, []).append(c.name)
    q = (args.query or "").lower()
    for name, val in sorted(table.items(), key=lambda kv: kv[1] & 0xFF):
        hexv = hexify.to_hex(val)
        if q and q not in name.lower() and q not in hexv.lower():
            continue
        line = f"{hexv}  {name}"
        if args.usage:
            line += f"    {', '.join(used.get(hexv, [])) or '(no command class found)'}"
        print(line)


def cmd_xref(args) -> None:
    idx = _idx(args)
    fqn = _resolve_one(idx, args.target)
    print(f"# {fqn}\n")
    if args.direction in ("up", "both"):
        up = trace.callers(idx, fqn, scope=args.scope)
        print(f"## referenced by ({len(up)})")
        for f in up[: args.limit]:
            print(f"  {f}")
        print()
    if args.direction in ("down", "both"):
        down = trace.callees(idx, fqn, app_only=not args.all)
        print(f"## references ({len(down)})")
        for f in down[: args.limit]:
            print(f"  {f}")


def cmd_trace(args) -> None:
    idx = _idx(args)
    fqn = _resolve_one(idx, args.target)
    nodes = trace.walk(
        idx,
        fqn,
        depth=args.depth,
        direction=args.direction,
        limit=args.limit,
        skip=() if args.no_skip else tuple(args.skip) if args.skip else None,
    )
    print(trace.tree(nodes))
    print(f"\n# {len(nodes)} classes, depth {args.depth}, direction {args.direction}", file=sys.stderr)


def cmd_process(args) -> None:
    idx = _idx(args)
    fqn = _resolve_one(idx, args.target)
    out = trace.process(idx, fqn, depth=args.depth, limit=args.limit)
    if args.output:
        Path(args.output).write_text(out + "\n")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)


# --- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cyncdec",
        description="Read, search and extract from the decompiled Cync Android app.",
        epilog="Run any subcommand with -h for its options.",
    )
    p.add_argument("--root", help="decompile root (default: autodetect, or $CYNCDEC_ROOT)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("map", help="what is in this tree, app vs vendor")
    m.add_argument("--deep", type=int, nargs="?", const=25, default=0, metavar="N",
                   help="also break the app packages down, N deepest")
    m.add_argument("--refresh", action="store_true", help="rebuild the class index")
    m.set_defaults(func=cmd_map)

    f = sub.add_parser("find", help="locate a class by name or partial FQN")
    f.add_argument("query")
    f.add_argument("--all", action="store_true", help="include vendor packages")
    f.add_argument("--limit", type=int, default=40)
    f.set_defaults(func=cmd_find)

    g = sub.add_parser("grep", help="full-text search, app packages only by default")
    g.add_argument("pattern")
    g.add_argument("--scope", default="app", help="app | vendor | all | a path prefix")
    g.add_argument("-e", "--regex", action="store_true")
    g.add_argument("-i", "--ignore-case", action="store_true")
    g.add_argument("--limit", type=int, default=200)
    g.set_defaults(func=cmd_grep)

    r = sub.add_parser("read", help="readable view of one class (hex bytes, names resolved)")
    r.add_argument("target", help="class name, FQN, or a file path")
    r.add_argument("--raw", action="store_true", help="keep JADX noise and annotations")
    r.add_argument("--no-hex", action="store_true")
    r.add_argument("--inline-hex", action="store_true", help="rewrite literals to hex in place")
    r.add_argument("--no-names", action="store_true", help="skip the Kotlin name header")
    r.add_argument("--no-enums", action="store_true", help="skip enum constant annotation")
    r.set_defaults(func=cmd_read)

    n = sub.add_parser("names", help="recovered Kotlin + enum names for one class")
    n.add_argument("target")
    n.set_defaults(func=cmd_names)

    e = sub.add_parser("enums", help="tree-wide obfuscated-field -> enum constant map")
    e.add_argument("query", nargs="?", help="substring filter")
    e.add_argument("--all", action="store_true", help="include unqualified entries")
    e.add_argument("--refresh", action="store_true")
    e.set_defaults(func=cmd_enums)

    o = sub.add_parser("opcodes", help="regenerate the mesh command catalogue")
    o.add_argument("--json", action="store_true")
    o.add_argument("--table", action="store_true", help="terse opcode/name columns")
    o.add_argument("--family", help="filter by command family (e.g. light, switch, fan, tile)")
    o.add_argument("-o", "--output", help="write to a file instead of stdout")
    o.set_defaults(func=cmd_opcodes)

    dev = sub.add_parser("devices", help="list Cync hardware device types and capabilities")
    dev.add_argument("query", nargs="?", help="filter by device type name")
    dev.add_argument("--category", help="filter by category (Light, Switch, Plug, Fan, Thermostat, Sensor)")
    dev.set_defaults(func=cmd_devices)

    srv = sub.add_parser("services", help="list decompiled Cync Service classes and handlers")
    srv.add_argument("query", nargs="?", help="filter by service name or category")
    srv.set_defaults(func=cmd_services)

    cc = sub.add_parser("codes", help="the XlinkCommandCode op namespace")
    cc.add_argument("query", nargs="?", help="filter by name or hex value")
    cc.add_argument("--usage", action="store_true",
                    help="also show which command class sends each op")
    cc.set_defaults(func=cmd_codes)

    x = sub.add_parser("xref", help="who references this class, and what it references")
    x.add_argument("target")
    x.add_argument("-d", "--direction", choices=["up", "down", "both"], default="both")
    x.add_argument("--scope", default="app")
    x.add_argument("--all", action="store_true")
    x.add_argument("--limit", type=int, default=60)
    x.set_defaults(func=cmd_xref)

    t = sub.add_parser("trace", help="walk the reference graph as a tree")
    t.add_argument("target")
    t.add_argument("--depth", type=int, default=2)
    t.add_argument("-d", "--direction", choices=["up", "down"], default="down")
    t.add_argument("--limit", type=int, default=400)
    t.add_argument("--skip", nargs="*", help="substrings of FQNs to prune (replaces the default set)")
    t.add_argument("--no-skip", action="store_true",
                   help="include generated Dagger/Hilt/databinding classes")
    t.set_defaults(func=cmd_trace)

    pr = sub.add_parser("process", help="markdown write-up of a whole flow")
    pr.add_argument("target")
    pr.add_argument("--depth", type=int, default=2)
    pr.add_argument("--limit", type=int, default=40)
    pr.add_argument("-o", "--output")
    pr.set_defaults(func=cmd_process)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
