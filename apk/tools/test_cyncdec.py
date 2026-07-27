"""Smoke tests for the cyncdec toolkit.

Run: python3 tools/test_cyncdec.py

Two kinds of test here. The pure ones (hex conversion, metadata parsing, enum
recovery) run on inline fixtures and always apply. The tree tests assert
against facts in this specific decompile - `SetBrightnessCommand` really does
carry opcode D2 11 02 - so they catch both a regression in the tools and a
silently swapped-out decompile.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cyncdec import enums, hexify, index, kmeta, opcodes, paths, render, trace

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {got.__class__.__name__} {want!r}")
        FAILED.append(name)


def check_true(name: str, cond, detail: str = "") -> None:
    check(name, bool(cond) or detail or False, True)


# --- pure unit tests ---------------------------------------------------------


def test_hexify() -> None:
    print("hexify")
    check("negative byte", hexify.to_hex(-46), "0xD2")
    check("positive byte", hexify.to_hex(17), "0x11")
    check("zero", hexify.to_hex(0), "0x00")
    check(
        "array annotation",
        hexify.annotate_line("        f1 = new byte[]{-46, 17, 2};"),
        "        f1 = new byte[]{-46, 17, 2};  // hex: {D2 11 02}",
    )
    check(
        "cast annotation",
        hexify.annotate_line("send((byte) -46, x);"),
        "send((byte) -46, x);  // hex: 0xD2",
    )
    check(
        "idempotent",
        hexify.annotate_line(hexify.annotate_line("f = new byte[]{-46, 17};")),
        hexify.annotate_line("f = new byte[]{-46, 17};"),
    )
    check("comment line untouched", hexify.annotate_line("// {-1, 2}"), "// {-1, 2}")
    check(
        "inline rewrite",
        hexify.inline("new byte[]{-46, 17, 2}"),
        "new byte[]{0xD2, 0x11, 0x02}",
    )


def test_kmeta() -> None:
    print("kmeta")
    sample = (
        '@Metadata(m28775d1 = {"\\u0000"}, m28776d2 = {"Lcom/foo/Bar$Companion;", "", "()V",'
        ' "OPCODE", "", "OPCODE_BYTES", "", "ge-sdk_release"}, m28777k = 1)\n'
        "public static final class Companion {\n}"
    )
    blocks = kmeta.parse(sample)
    check("one block", len(blocks), 1)
    check("class recovered", blocks[0].kotlin_class, "com.foo.Bar$Companion")
    check("names recovered", blocks[0].names, ["OPCODE", "OPCODE_BYTES"])
    check("owner attributed", blocks[0].owner, "Companion")
    check("module name dropped", "ge-sdk_release" in blocks[0].names, False)
    check("no metadata -> empty", kmeta.parse("class Foo {}"), [])


def test_enums() -> None:
    print("enums")
    sample = """
    public static final WriteType f34481a;
    public static final WriteType f34482b;
    static {
        WriteType writeType = new WriteType("DEFAULT", 0);
        f34481a = writeType;
        WriteType writeType2 = new WriteType("NO_ACKNOWLEDGEMENT", 1);
        f34482b = writeType2;
    }
    """
    got = enums.parse(sample)
    check("two constants", len(got), 2)
    check("first", got["f34481a"], ("WriteType", "DEFAULT", 0))
    check("second", got["f34482b"], ("WriteType", "NO_ACKNOWLEDGEMENT", 1))
    check(
        "annotation",
        enums.annotate_line(
            "        this.f1 = DeviceCommand.WriteType.f34482b;",
            {"WriteType.f34482b": "NO_ACKNOWLEDGEMENT"},
        ),
        "        this.f1 = DeviceCommand.WriteType.f34482b;  // WriteType.f34482b = NO_ACKNOWLEDGEMENT",
    )


def test_paths() -> None:
    print("paths")
    check("app", paths.classify("com/gelighting/cbygekit/Foo.java"), "app")
    check("near", paths.classify("io/xlink/wifi/Foo.java"), "near")
    check("vendor", paths.classify("okhttp3/Foo.java"), "vendor")
    check("vendor nested", paths.classify("com/thingclips/a/Foo.java"), "vendor")
    check("obfuscated pkg", paths.classify("p073d/Foo.java"), "vendor")
    check("unknown", paths.classify("brandnew/Foo.java"), "unknown")
    check("is_app spans near", paths.is_app("io/xlink/wifi/Foo.java"), True)


def test_render() -> None:
    print("render")
    src = (
        "import kotlin.Metadata;\n"
        '@Metadata(m28776d2 = {"Lcom/foo/Bar;", "ge-sdk_release"})\n'
        "public final class Bar {\n"
        "    /* JADX INFO: renamed from: r */\n"
        "    public static final byte[] f1 = new byte[]{-46, 17};\n"
        "}"
    )
    out = render.render(src, show_names=False)
    check("metadata line gone", "@Metadata" in out, False)
    check("metadata import gone", "import kotlin.Metadata" in out, False)
    check("jadx comment folded", "// was: r" in out, True)
    check("hex added", "// hex: {D2 11}" in out, True)
    check("code kept", "public final class Bar" in out, True)


# --- tests against this tree -------------------------------------------------


def test_tree() -> None:
    print("tree (this decompile)")
    idx = index.build()
    check_true("index is populated", len(idx.by_fqn) > 20000, f"only {len(idx.by_fqn)}")

    hits = idx.resolve("SetBrightnessCommand")
    check("resolves a known class", len(hits), 1)
    check(
        "to the right path",
        idx.by_fqn[hits[0]],
        "com/gelighting/cbygekit/services/devices/command/SetBrightnessCommand.java",
    )

    dm = opcodes._delegate_methods(idx)
    check_true("telink send method found", dm.telink_send, "none derived")
    check_true("xlink opcode methods found", dm.xlink_send_with_opcode, "none derived")

    all_cmds = opcodes.extract_all(idx)
    cmds = {c.name: c for c in all_cmds}
    check_true("command classes found", len(cmds) > 100, f"only {len(cmds)}")

    # Scaffolding must not be counted as commands with a missing opcode.
    check("abstract base classified", cmds["DeviceCommand"].kind, "base")
    check("interface classified", cmds["ISetColorCommand"].kind, "interface")
    check("Kotlin helper classified", cmds["DeviceCommandKt"].kind, "helper")
    check("a real command stays one", cmds["SetBrightnessCommand"].kind, "command")
    check_true(
        "scaffolding is a minority",
        0 < sum(1 for c in all_cmds if not c.is_real_command) < len(all_cmds) / 3,
        "classification looks wrong",
    )

    sb = cmds["SetBrightnessCommand"]
    check("brightness telink opcode", sb.telink_opcode, "D2 11 02")
    check("brightness xlink opcode", sb.xlink_opcode, "D2 11 02")
    check("brightness outer op", sb.xlink_outer_opcode, "0xD2")
    check("brightness write type resolved", sb.write_type, "NO_ACKNOWLEDGEMENT")

    # Resolved only by following the payload-builder helper, not an inline ref.
    check("power state via helper", cmds["SetPowerStateCommand"].telink_opcode, "D0 11 02")

    # The case that motivated per-transport opcodes at all.
    fan = cmds["SetFanSpeedCommand"]
    check("fan telink", fan.telink_opcode, "F4 11 02 01")
    check("fan xlink", fan.xlink_opcode, "E2 11 02 06")
    check("fan flagged as split", fan.transports_disagree, True)

    # Opcodes derived by hand during the 2026-07-25 agent research pass, kept
    # here as regression anchors. The tool and the manual reads agreed on all
    # of these independently; a future change that breaks one is a real
    # regression, not a cosmetic diff.
    for name, want in {
        # chunked sends - opcode reaches the chunker via a shared builder
        "SetLightShowCommand": "F7 11 02 43",
        "SetLightShowExtendedCommand": "F7 11 02 57",
        "SetMusicShowCommand": "F7 11 02 44",
        "SetMusicShowExtendedCommand": "F7 11 02 58",
        "SetTileLayoutCommand": "F7 11 02 53",
        "SetMultiColorBitmapCommand": "F7 11 02 4F",
        "SetCustomButtonOptionCommand": "F7 11 02 2C",
        "SetSameGroupDeviceIdsCommand": "F7 11 02 61",
        "SetMeshAddressCommand": "E0 11 02",
        "SetWifiCommand": "F6 11 02 02",
        # arrays that were invisible until symbolic constants were resolved
        # (R8 merged the literal 16 into BouncyCastle's Tnaf.POW_2_WIDTH)
        "QueryDeviceTypeAndVersionCommand": "C7 11 02 10 00",
        "QueryFirmwareVersionCommand": "EA 11 02 10 05",
        "QueryHardwareVersionCommand": "EA 11 02 10 F4",
    }.items():
        check(f"opcode {name}", cmds[name].telink_opcode, want)

    # Hub / scalar-op family: no mesh array, op handed to the frame builder.
    # Cross-checked against the manual hub-command pass; 17/17 agreed.
    for name, want in {
        "SearchDevicesHubCommand": "0x06",
        "CreateSceneHubCommand": "0x10",  # via `new Frame(...)` + named enum code
        "DeleteSceneHubCommand": "0x1F",
        "CreateGroupHubCommand": "0x30",  # ditto
        "QueryHubInfoCommand": "0x4B",
        "QueryHubDeviceListCommand": "0x51",
        "QueryMeshStatusCommand": "0x52",
        "CreateScheduleHubCommand": "0x92",
        "ToggleAutomationHubCommand": "0x93",
        "AddAutomationHubCommand": "0x95",
        "DeleteAutomationHubCommand": "0x97",  # undocumented before this pass
        "MeshStatusProxyHeartbeatCommand": "0xAF",
    }.items():
        check(f"scalar op {name}", cmds[name].scalar_op, want)

    # A scalar op must never be reported as a mesh opcode array - different
    # wire shape, no 7-byte routing prefix.
    check("scalar op is not a mesh opcode", cmds["DeleteSceneHubCommand"].opcode_bytes, None)

    # Opcode inherited from a parent that owns the send.
    check("inherited opcode", cmds["AddDeviceGroupCommand"].telink_opcode, "D7 11 02")
    check(
        "inheritance is attributed",
        cmds["AddDeviceGroupCommand"].inherited_from,
        "ControlDeviceGroupCommand",
    )

    # Runtime-selected op: reporting a single value would be wrong.
    check(
        "runtime op variants",
        cmds["SetAmazonTokenCommand"].op_variants,
        {"SET_AVS_ACCESS_TOKEN": "0xA2", "SET_AVS_REFRESH_TOKEN": "0xA3"},
    )

    # A decorator forwarding to a wrapped command has no opcode by construction.
    check("decorator classified", cmds["IgnoreResultDeviceCommand"].kind, "wrapper")

    codes = opcodes.command_code_table(idx)
    check_true("command code table", len(codes) >= 30, f"only {len(codes)}")
    check("code table resolves symbolic", codes.get("HUB_CREATE_SCENE"), 16)  # Tnaf.POW_2_WIDTH
    check("code table signed byte", codes.get("HUB_CREATE_GROUP"), 48)

    real = [c for c in all_cmds if c.is_real_command]
    resolved = sum(1 for c in real if c.resolved)
    check("every real command resolved", resolved, len(real))

    emap = enums.build_map(idx)
    check("enum map: write type", emap.get("WriteType.f34482b"), "NO_ACKNOWLEDGEMENT")
    check_true("enum map is substantial", len(emap) > 1500, f"only {len(emap)}")

    callers = trace.callers(idx, hits[0])
    check_true("brightness has callers", callers, "none found")
    callees = trace.callees(idx, hits[0])
    check_true(
        "brightness references the delegates",
        any("TelinkCommandDelegate" in c for c in callees),
        f"got {callees}",
    )

    nodes = trace.walk(idx, hits[0], depth=2, limit=20)
    check_true("walk returns a graph", len(nodes) > 5, f"only {len(nodes)}")
    check("walk root has no parent", nodes[0].via, None)
    rendered = trace.tree(nodes)
    check_true("tree renders", "SetBrightnessCommand" in rendered, "missing root")


def main() -> int:
    for t in (test_hexify, test_kmeta, test_enums, test_paths, test_render, test_tree):
        t()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {', '.join(FAILED)}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
