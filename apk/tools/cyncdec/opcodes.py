"""Extract the mesh command catalogue from `services/devices/command/`.

Every device command in ge-sdk follows one shape: a static `byte[]` opcode
constant, a `sendTelinkRequest` that pushes `opcode + payload` at the BLE mesh
delegate, and a `sendXlinkRequest` that pushes the same payload at the Xlink
delegate behind a *separate* one-byte outer opcode. This walks all ~196 of them
and tabulates that, so the catalogue can be regenerated instead of hand-copied
into docs/mesh_opcodes.md.

The delegate method names are obfuscated (`mo14046d`, `m14392a`) and will
change on any re-decompile, so they are *derived* at runtime from the delegate
interfaces rather than hardcoded. See `_delegate_methods`.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import consts, hexify, index, kmeta

COMMAND_PKG = "com/gelighting/cbygekit/services/devices/command"
TELINK_DELEGATE = "com.gelighting.cbygekit.services.devices.telink.TelinkCommandDelegate"
XLINK_DELEGATE = "com.gelighting.cbygekit.services.devices.xlink.XlinkCommandDelegate"

_CLASS_DECL = re.compile(
    r"^public\s+(?:final\s+|abstract\s+)*(?:/\*.*?\*/\s*)?class\s+(\w+)"
    r"(?:\s+extends\s+([\w.]+))?(?:\s+implements\s+([\w.,\s]+))?",
    re.MULTILINE,
)
# Elements may be symbolic, not just numeric - R8 constant-merging leaves
# things like `Tnaf.POW_2_WIDTH` (== 16) inside opcode arrays. See consts.py.
_BYTE_ARRAY_ASSIGN = re.compile(
    r"(\w+)\s*=\s*(?:new\s+byte\[\]\s*)?\{([^}]*)\}"
)
_METHOD = re.compile(r"^\s{4}(?:public|protected|private).*?\b(\w+)\s*\(", re.MULTILINE)


@dataclass
class DelegateMethods:
    """Obfuscated delegate method names, resolved from the interface sources."""

    telink_send: set[str] = field(default_factory=set)
    xlink_send_with_opcode: set[str] = field(default_factory=set)
    xlink_send_raw: set[str] = field(default_factory=set)


def _delegate_methods(idx: index.Index) -> DelegateMethods:
    """Find which delegate methods carry an opcode byte, by reading signatures.

    A method taking `(byte b, byte[] bArr, ...)` sends payload under an outer
    opcode; one taking `(byte[] bArr, ...)` sends a pre-framed payload.
    """
    dm = DelegateMethods()
    sig = re.compile(r"\b(\w+)\s*\(([^)]*)\)")

    for fqn, bucket_op, bucket_raw in (
        (TELINK_DELEGATE, None, dm.telink_send),
        (XLINK_DELEGATE, dm.xlink_send_with_opcode, dm.xlink_send_raw),
    ):
        rel = idx.by_fqn.get(fqn)
        if not rel:
            continue
        for m in sig.finditer((idx.src / rel).read_text(errors="replace")):
            name, params = m.group(1), m.group(2)
            if not name.startswith(("mo", "m")) or "byte" not in params:
                continue
            # drop the synthetic receiver param DefaultImpls statics carry
            parts = [p.strip() for p in params.split(",") if p.strip()]
            parts = [p for p in parts if "CommandDelegate" not in p]
            if not parts:
                continue
            first = parts[0]
            has_payload = any("byte[]" in p for p in parts)
            if not has_payload:
                continue
            if re.match(r"byte\s+\w+$", first) and bucket_op is not None:
                bucket_op.add(name)
            else:
                bucket_raw.add(name)
    return dm


@dataclass
class Command:
    name: str
    path: str
    extends: str | None
    telink_opcode: str | None  # bytes sent down the BLE mesh path, "D2 11 02"
    xlink_opcode: str | None  # bytes sent down the Xlink path (often the same)
    all_byte_arrays: dict[str, str]  # every static byte[] in the class
    opcode_names: list[str]  # from @Metadata: OPCODE, OPCODE_BYTES ...
    xlink_outer_opcode: str | None  # "0xD2"
    xlink_path: str | None  # "opcode" (outer op + payload) or "raw" (pre-framed)
    telink: bool
    xlink: bool
    write_type: str | None
    notes: list[str]
    kind: str = "command"  # command | base | interface | helper
    scalar_op: str | None = None  # hub-style single-byte op, e.g. "0x97"
    inherited_from: str | None = None  # opcode came from this parent class
    # Ops chosen at runtime (e.g. SetAmazonTokenCommand picks ACCESS vs
    # REFRESH token). Reporting any single one of these would be wrong.
    op_variants: dict[str, str] = field(default_factory=dict)

    @property
    def is_real_command(self) -> bool:
        return self.kind == "command"

    @property
    def resolved(self) -> bool:
        """Do we know what this command puts on the wire?"""
        return bool(
            self.telink_opcode or self.xlink_opcode or self.scalar_op or self.op_variants
        )

    @property
    def opcode_bytes(self) -> str | None:
        """The command's mesh opcode array, when it has one.

        Deliberately does NOT fall back to `scalar_op`: a hub command's single
        op byte is not a mesh opcode and carries no 7-byte routing prefix.
        Conflating them would put a wrong shape on the wire.
        """
        return self.telink_opcode or self.xlink_opcode

    @property
    def transports_disagree(self) -> bool:
        return bool(
            self.telink_opcode
            and self.xlink_opcode
            and self.telink_opcode != self.xlink_opcode
        )


def _classify_kind(name: str, text: str) -> str:
    """Is this an actual command, or scaffolding around them?

    The command package also holds the abstract base classes, the interfaces
    they implement, and JADX's `*Kt` files for Kotlin top-level functions.
    Counting those as commands inflates the catalogue and, worse, parks them
    permanently in the "opcode unresolved" list where they look like
    outstanding research rather than things with no opcode by definition.
    """
    if name.endswith("Kt"):
        return "helper"
    # A decorator holding a `DeviceCommand<?>` and forwarding the send to it
    # has no opcode of its own - the wrapped command supplies it at runtime.
    if re.search(r"DeviceCommand<\?>\s+\w+;", text) and re.search(
        r"this\.\w+\.mo\d+\w?\(new \w*CommandDelegate", text
    ):
        return "wrapper"
    if re.search(r"^public\s+(?:\w+\s+)*interface\s+\w+", text, re.MULTILINE):
        return "interface"
    if re.search(r"^public\s+abstract\s+class\s+\w+", text, re.MULTILINE):
        return "base"
    return "command"


def _find_call_args(text: str, method: str) -> list[str]:
    """Return the argument text of each call to `method` in `text`."""
    out = []
    for m in re.finditer(re.escape(method) + r"\s*\(", text):
        i, depth = m.end(), 1
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        out.append(text[m.end() : i - 1])
    return out


def _method_body(text: str, name: str) -> str | None:
    """Body of the method declared as `name(...)`, by brace matching."""
    # `... m14114x() throws IOException {` - the throws clause is common here,
    # since these payload builders write to a DataOutputStream.
    decl = re.search(rf"\b{re.escape(name)}\s*\([^)]*\)\s*(?:throws\s+[\w.,\s]+?)?\{{", text)
    if not decl:
        return None
    i, depth = decl.end(), 1
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[decl.end() : i - 1]


def _method_body_with_param(text: str, param_type: str) -> str | None:
    """Body of the method whose parameter list mentions `param_type`.

    Used to find each transport's send override without depending on its
    R8-renamed name, which renumbers on every re-decompile.
    """
    decl = (
        r"\b\w+\s*\([^)]*"
        + re.escape(param_type)
        + r"[^)]*\)\s*(?:throws\s+[\w.,\s]+?)?\{"  # these sends declare `throws IOException`
    )
    for m in re.finditer(decl, text):
        i, depth = m.end(), 1
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[m.end() : i - 1]
        if body.strip():
            return body
    return None


# Classes that build a complete Xlink wire frame. The command op travels as an
# argument to these rather than to a delegate method, which is why the hub
# family looked opcode-less: `m14449a(msgId, (byte) 31, buf)` is DeleteScene.
FRAMER_CLASSES = ("XlinkTranslatorKt", "Frame", "Xlink")


XLINK_CODE_ENUM = "com.gelighting.cbygekit.services.devices.xlink.XlinkCommandCode"

# `HUB_CREATE_GROUP((byte) 48),` / `HUB_CREATE_SCENE(Tnaf.POW_2_WIDTH),`
_ENUM_CODE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*\(\s*(?:\(byte\)\s*)?(-?\w[\w.]*)\s*\)", re.MULTILINE)


def command_code_table(
    idx: index.Index, resolver: consts.ConstResolver | None = None
) -> dict[str, int]:
    """`XlinkCommandCode` constant name -> byte value.

    This enum is the hub/Xlink command namespace. Values are written either as
    a byte literal or, thanks to R8 constant-merging, as a symbolic constant
    from an unrelated vendor class - so symbols are resolved through consts.
    """
    rel = idx.by_fqn.get(XLINK_CODE_ENUM)
    if not rel:
        return {}
    # Several entries are symbolic (R8 merged the literal into a vendor class),
    # so a caller that omits the resolver would silently get a short table.
    resolver = resolver or consts.ConstResolver(idx)
    try:
        text = (idx.src / rel).read_text(errors="replace")
    except OSError:
        return {}

    local = consts.local_constants(text)
    out: dict[str, int] = {}
    for m in _ENUM_CODE.finditer(text):
        name, raw = m.group(1), m.group(2)
        if re.fullmatch(r"-?\d+", raw):
            out[name] = int(raw)
        elif re.fullmatch(r"-?0[xX][0-9a-fA-F]+", raw):
            out[name] = int(raw, 16)
        elif raw in local:
            out[name] = local[raw]
        elif resolver:
            v = resolver.resolve(raw)
            if v is not None:
                out[name] = v
    return out


def _scalar_op_in(
    body: str, codes: dict[str, int] | None = None, resolver: consts.ConstResolver | None = None
) -> str | None:
    """The single-byte command op passed to a frame builder in this body.

    Only looks inside framer call arguments. A bare scan for `(byte) N` would
    also hit payload writes like `writeBuffer.write((byte) 0)` and report a
    padding byte as the opcode.
    """
    found: set[int] = set()
    patterns = [rf"\b{f}\.\w+\s*\(" for f in FRAMER_CLASSES]
    # `new Frame(msgId, Direction.REQ, XlinkCommandCode.HUB_CREATE_GROUP, req)`
    patterns += [rf"\bnew\s+{f}\s*\(" for f in FRAMER_CLASSES]
    for pat in patterns:
        for m in re.finditer(pat, body):
            i, depth = m.end(), 1
            while i < len(body) and depth:
                if body[i] == "(":
                    depth += 1
                elif body[i] == ")":
                    depth -= 1
                i += 1
            args = body[m.end() : i - 1]
            for c in re.finditer(r"\(byte\)\s*(-?\d+)", args):
                found.add(int(c.group(1)))
            # named op, e.g. `XlinkCommandCode.HUB_CREATE_GROUP`
            for c in re.finditer(r"\b([A-Z][A-Z0-9_]{3,})\b", args):
                if codes and c.group(1) in codes:
                    found.add(codes[c.group(1)])
                elif resolver:
                    v = resolver.resolve(f"XlinkCommandCode.{c.group(1)}")
                    if v is not None:
                        found.add(v)
    return hexify.to_hex(found.pop()) if len(found) == 1 else None


def extract_one(
    rel: str,
    text: str,
    dm: DelegateMethods,
    emap: dict[str, str] | None = None,
    resolver: consts.ConstResolver | None = None,
    codes: dict[str, int] | None = None,
) -> Command:
    decl = _CLASS_DECL.search(text)
    name = Path(rel).stem
    extends = decl.group(2) if decl else None

    local = consts.local_constants(text)
    arrays: dict[str, list[int]] = {}
    for m in _BYTE_ARRAY_ASSIGN.finditer(text):
        parsed = consts.parse_byte_array(m.group(2), resolver, local)
        if parsed is not None:
            arrays[m.group(1)] = parsed
    hex_arrays = {k: " ".join(f"{n & 0xFF:02X}" for n in v) for k, v in arrays.items()}

    def _array_in(snippet: str) -> str | None:
        """The one static array referenced here, or None if 0 or several.

        Returning None on ambiguity matters: ExecuteSceneCommand's BLE path
        picks between two arrays by branch, and reporting whichever the scan
        happened to hit first would be a plausible-looking wrong answer.
        """
        found = {hex_arrays[i] for i in re.findall(r"\b(\w+)\b", snippet) if i in hex_arrays}
        return found.pop() if len(found) == 1 else None

    def opcode_for(delegate_type: str, methods: set[str]) -> str | None:
        """Which static array does this transport's send path actually use?

        Scoped to the send method for *this* transport, found by its parameter
        type (`TelinkCommandDelegate` / `XlinkCommandDelegate`) rather than by
        the R8-renamed method name. A class can define several arrays -
        SetFanSpeedCommand sends `F4 11 02 01` over BLE mesh but `E2 11 02 06`
        over Xlink - so anything less precise reports one transport's opcode
        as both.

        Within that body, three shapes are handled:
          1. the array passed straight to the delegate call
          2. the array reached through one payload-builder helper hop
             (`delegate.send(buildPayload(), ...)`)
          3. the array as first argument to the chunking helper
             (`DeviceCommand.sendBlocks(OPCODE, payload, blockSize, ...)`),
             which is how every multi-packet command sends - light shows,
             tile layouts, bitmaps. Those never touch the delegate directly.
        """
        body = _method_body_with_param(text, delegate_type)
        if body is None:
            return None

        direct = _array_in(body)
        if direct:
            return direct

        for meth in sorted(methods):
            for args in _find_call_args(body, meth):
                got = _array_in(args)
                if got:
                    return got

        # Follow every private helper the send body calls, one hop. The show
        # commands route both transports through a shared builder that makes
        # the chunked-send call, so the array is never named in the send body
        # itself. Ambiguity across hops yields None, as everywhere else.
        found: set[str] = set()
        for helper in dict.fromkeys(re.findall(r"\b(m\d+\w*)\s*\(", body)):
            hbody = _method_body(text, helper)
            if hbody:
                via = _array_in(hbody)
                if via:
                    found.add(via)
        return found.pop() if len(found) == 1 else None

    telink_opcode = opcode_for("TelinkCommandDelegate", dm.telink_send)
    xlink_opcode = opcode_for(
        "XlinkCommandDelegate", dm.xlink_send_with_opcode | dm.xlink_send_raw
    )


    meta_names = [
        n
        for b in kmeta.parse(text)
        for n in b.names
        if "OPCODE" in n.upper() or "CMD" in n.upper()
    ]

    xlink_outer = None
    xlink_path = None
    for meth in sorted(dm.xlink_send_with_opcode):
        for args in _find_call_args(text, meth):
            xlink_path = "opcode"
            m = re.search(r"\(byte\)\s*(-?\d+)", args)
            if m:
                xlink_outer = hexify.to_hex(int(m.group(1)))
                break
            # `m14392a(delegate, OPCODE_BYTES[0], ...)` - resolve through the
            # static array we already parsed.
            m = re.search(r"(\w+)\[0\]", args)
            if m and m.group(1) in arrays:
                xlink_outer = hexify.to_hex(arrays[m.group(1)][0])
                break
        if xlink_outer:
            break
    if xlink_path is None and any(m in text for m in dm.xlink_send_raw):
        # Payload is already framed; the opcode is the head of the byte array.
        xlink_path = "raw"

    # Hub-style commands carry no mesh array at all - their op is a scalar
    # handed to the frame builder, or to an opcode-carrying delegate method.
    scalar_op = None
    if not telink_opcode and not xlink_opcode:
        xbody = _method_body_with_param(text, "XlinkCommandDelegate")
        if xbody:
            scalar_op = _scalar_op_in(xbody, codes, resolver)
        # With no mesh array in play, the byte handed to an opcode-carrying
        # delegate method IS the command op - there is nothing for it to be a
        # wrapper around. (When an array does exist that same byte is an outer
        # wrapper, usually 0x8E, and must not be reported as the opcode.)
        if scalar_op is None and xlink_outer:
            scalar_op = xlink_outer

    # A command may select its op at runtime from a named-code enum. Capture
    # the whole candidate set rather than picking one arbitrarily.
    op_variants: dict[str, str] = {}
    if not telink_opcode and not xlink_opcode and scalar_op is None and codes:
        for m in re.finditer(r"XlinkCommandCode\.([A-Z][A-Z0-9_]+)", text):
            if m.group(1) in codes:
                op_variants[m.group(1)] = hexify.to_hex(codes[m.group(1)])

    telink = any(m in text for m in dm.telink_send) or "sendTelinkRequest" in text
    xlink = xlink_path is not None

    wt = re.search(r"DeviceCommand\.WriteType\.(\w+)", text)

    notes = [
        l.strip().lstrip("/* ").rstrip("*/ ")
        for l in text.splitlines()
        if "cync-lan reverse-engineering note" in l.lower()
    ]

    return Command(
        name=name,
        path=rel,
        extends=extends,
        telink_opcode=telink_opcode,
        xlink_opcode=xlink_opcode,
        all_byte_arrays=hex_arrays,
        opcode_names=meta_names,
        xlink_outer_opcode=xlink_outer,
        xlink_path=xlink_path,
        telink=telink,
        xlink=xlink,
        write_type=(emap or {}).get(f"WriteType.{wt.group(1)}", wt.group(1)) if wt else None,
        notes=notes,
        kind=_classify_kind(name, text),
        scalar_op=scalar_op,
        op_variants=op_variants,
    )


def _sibling_send_sources(idx: index.Index, rel: str, pkg: str) -> str:
    """Bodies of a command's `$sendTelinkRequest$N` / `$sendXlinkRequest$N` files.

    JADX splits Kotlin suspend lambdas into their own class files, so for the
    chunked commands (light shows, tile layouts, bitmaps) the delegate call
    lives outside the class body entirely. The opcode array is still a static
    field of the outer class, so appending these bodies lets the normal
    resolution find it.
    """
    stem = Path(rel).stem
    out = []
    for other in idx.files(pkg):
        oname = Path(other).stem
        if oname.startswith(stem + "$") and "send" in oname:
            try:
                out.append((idx.src / other).read_text(errors="replace"))
            except OSError:
                pass
    return "\n".join(out)


def extract_all(idx: index.Index, pkg: str = COMMAND_PKG) -> list[Command]:
    from . import enums

    dm = _delegate_methods(idx)
    emap = enums.build_map(idx)
    resolver = consts.ConstResolver(idx)
    codes = command_code_table(idx, resolver)
    cmds: list[Command] = []
    for rel in sorted(idx.files(pkg)):
        stem = Path(rel).stem
        if "$" in stem or not stem.endswith(("Command", "CommandKt")):
            continue
        text = (idx.src / rel).read_text(errors="replace")
        siblings = _sibling_send_sources(idx, rel, pkg)
        cmds.append(
            extract_one(rel, text + siblings, dm, emap, resolver, codes)
        )

    _inherit_opcodes(cmds)
    return cmds


def _inherit_opcodes(cmds: list[Command]) -> None:
    """Fill in opcodes for subclasses that send through their parent.

    `AddDeviceGroupCommand extends ControlDeviceGroupCommand` overrides only
    the parameters - the send, and therefore the opcode, lives in the parent.
    Marked with `inherited_from` so the catalogue never presents a borrowed
    opcode as if it were read from the class itself.
    """
    by_name = {c.name: c for c in cmds}
    for c in cmds:
        if c.resolved or not c.extends:
            continue
        seen = set()
        parent = by_name.get(c.extends.rsplit(".", 1)[-1])
        while parent and parent.name not in seen:
            seen.add(parent.name)
            if parent.resolved:
                c.telink_opcode = parent.telink_opcode
                c.xlink_opcode = parent.xlink_opcode
                c.scalar_op = parent.scalar_op
                c.xlink_outer_opcode = c.xlink_outer_opcode or parent.xlink_outer_opcode
                c.inherited_from = parent.name
                break
            parent = by_name.get((parent.extends or "").rsplit(".", 1)[-1])


# --- output formats ----------------------------------------------------------


def to_markdown(cmds: list[Command], src_label: str = "cync_decompiled_v2") -> str:
    real = [c for c in cmds if c.is_real_command]
    scaffolding = [c for c in cmds if not c.is_real_command]

    mesh = [c for c in real if c.opcode_bytes]
    scalar = [c for c in real if not c.opcode_bytes and (c.scalar_op or c.op_variants)]
    unresolved = [c for c in real if not c.resolved]

    def note(c: Command) -> str:
        bits = []
        if c.inherited_from:
            bits.append(f"inherited from `{c.inherited_from}`")
        if c.notes:
            bits.append("has inline research note")
        return "; ".join(bits) or ""

    body = [
        "## Mesh commands",
        "",
        "Commands carrying a BTLE-mesh opcode array. The two opcode columns are the",
        "static array that *that transport's own send call* passes - several classes",
        "define one array per transport and they do not always match. `Xlink outer op`",
        "is the single byte the Xlink transport wraps the mesh payload in; where that",
        "is `0x8E` the mesh array travels as opaque payload under the relay.",
        "",
        "| Command | Telink (BLE mesh) | Xlink | Xlink outer op | Write type | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in sorted(mesh, key=lambda c: (c.opcode_bytes or "", c.name)):
        body.append(
            f"| `{c.name}` | `{c.telink_opcode or '-'}` | `{c.xlink_opcode or '-'}` "
            f"| `{c.xlink_outer_opcode or '-'}` | {c.write_type or '-'} | {note(c)} |"
        )

    body += [
        "",
        "## Hub / scalar-op commands",
        "",
        "These carry **no mesh opcode array**. The op is a single byte handed to the",
        "Xlink frame builder, and the payload follows it directly - there is no 7-byte",
        "mesh routing prefix. Do not send these in the mesh envelope shape.",
        "",
        "| Command | Op | Notes |",
        "| --- | --- | --- |",
    ]
    for c in sorted(scalar, key=lambda c: (c.scalar_op or "", c.name)):
        if c.op_variants:
            variants = ", ".join(f"`{v}` ({k})" for k, v in sorted(c.op_variants.items()))
            body.append(f"| `{c.name}` | {variants} | selected at runtime; {note(c)} |")
        else:
            body.append(f"| `{c.name}` | `{c.scalar_op}` | {note(c)} |")

    split = [c for c in real if c.transports_disagree]
    if split:
        body += [
            "",
            "## Commands that send a different opcode per transport",
            "",
            "Reading only one of the two send paths gives the wrong opcode for these.",
            "",
        ]
        for c in split:
            body.append(
                f"- `{c.name}` - telink `{c.telink_opcode}`, xlink `{c.xlink_opcode}` ({c.path})"
            )

    if unresolved:
        body += [
            "",
            "## Unresolved",
            "",
            "Opcode could not be tied to a send call. Read by hand with `cyncdec read <name>`.",
            "",
        ]
        for c in sorted(unresolved, key=lambda c: c.name):
            arrays = ", ".join(f"`{k}={v}`" for k, v in list(c.all_byte_arrays.items())[:4])
            body.append(f"- `{c.name}` {arrays}")

    annotated = [c for c in real if c.notes]
    if annotated:
        body += ["", "## Commands carrying inline research notes", ""]
        for c in annotated:
            body.append(f"- `{c.name}` ({c.path})")

    if scaffolding:
        body += [
            "",
            "## Not commands",
            "",
            "Base classes, interfaces, Kotlin top-level helper files, and one decorator",
            "that forwards to a wrapped command. Listed so they are not mistaken for",
            "commands with a missing opcode - they have none by construction.",
            "",
        ]
        for c in sorted(scaffolding, key=lambda c: (c.kind, c.name)):
            body.append(f"- `{c.name}` ({c.kind})")

    resolved = sum(1 for c in real if c.resolved)
    header = (
        f"<!-- generated by tools/cyncdec opcodes - do not hand-edit -->\n"
        f"# Mesh command catalogue ({src_label})\n\n"
        f"**{resolved} of {len(real)} command classes resolved** "
        f"({len(mesh)} with a mesh opcode array, {len(scalar)} hub/scalar-op), "
        f"plus {len(scaffolding)} base/interface/helper/wrapper files listed at the end.\n\n"
        f"Method and field names in the cited sources are R8-renamed and renumber on\n"
        f"every re-decompile - cite class paths, not member names.\n\n"
    )
    return header + "\n".join(body) + "\n"


def to_json(cmds: list[Command]) -> str:
    return json.dumps([asdict(c) for c in cmds], indent=2)
