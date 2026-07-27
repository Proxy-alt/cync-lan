# `research` — reverse-engineering the Cync Android app

This branch is not a release artifact. It holds the reverse-engineering
material behind the protocol documentation on `core`, `python` and
`feature/ha-custom-component`.

**The decompiled app is not here.** It is ~205 MB of Savant's compiled code,
and redistributing it is a different thing from documenting what the protocol
does. What is here is a recipe to regenerate it from an APK you supply, plus
the annotations and analysis to apply on top — the same shape Eaglercraft uses
for the same reason.

```bash
./apply.sh /path/to/cync.apk ./decompiled
```

That decompiles, verifies the tree came out right, and applies the annotation
patch. Then:

```bash
CYNCDEC_ROOT=./decompiled tools/cyncdec.sh map
CYNCDEC_ROOT=./decompiled tools/cyncdec.sh read SetBrightnessCommand
```

## The APK

| | |
|---|---|
| Package | `com.ge.cbyge` |
| Size | 175,320,244 bytes |
| SHA-256 | `3d57c08b5c180bf7872c91186a78dc60e2684fb92796feb78ac9163bf33ec06c` |

A different build will decompile to different line numbers and different
generated names. `apply.sh` warns rather than refusing, since a newer APK is
still worth looking at — just not against these line-numbered citations.

## The jadx invocation

```bash
jadx --comments-level debug --no-inline-anonymous --deobf -d out/ cync.apk
```

Verified with jadx **1.5.6**, producing 55,368 `.java` files.

These flags were never written down and were recovered by experiment —
regenerate, diff against the committed baseline, keep whatever moves the
output closer.

**`--comments-level debug` is the one that matters.** Without it jadx silently
drops the body of any method it fails to decompile, leaving only
`/* Method dump skipped, instruction units count: 653 */`. That hits **254
files**, including the four commissioning methods in `BleDeviceCommissionService`
that the BLE provisioning research could not read. jadx names the remedy in
the skip comment itself.

**`--no-inline-anonymous`** brings methods emitted as raw fallback from 19 down
to 10, matching the baseline.

**`--deobf`** is the one that is easy to rule out wrongly, because
`SetBrightnessCommand` and every other long name survives it. `--deobf-min`
defaults to 3, so only identifiers *shorter* than that get renamed — which is
where `C2551Ok`, `m28775d1` and `f31596n` come from (`Ok`, `d1`, `n`). If a
regenerated tree shows plain `n` and `Ok`, this flag is missing.

Tested and rejected, no effect on output: `--no-imports`,
`--no-replace-consts` (leaving it *off* is why `Tnaf.POW_2_WIDTH` appears
instead of a literal `16` — a detail that hid 16 opcode arrays from the
extractor until it was understood), `--show-bad-code`, `--no-inline-methods`,
and raising the JVM stack with `-Xss` up to 64 MB. jadx 1.5.5, 1.5.0 and 1.4.7
all land *further* from the baseline than 1.5.6, so the version is not the
discriminator.

## What reproduces, and what does not

Reproducing gives the same class set (55,368 files, matching file-for-file),
the same methods decompiling and falling back, and every identifier that was
longer than three characters before obfuscation.

What does **not** reproduce is jadx's generated names. The identical command
on the identical APK gives `C2551Ok` one run and `C3120Ok` the next; the
counter is assigned across the whole input and is not stable.

In practice this costs less than it sounds. The annotation patch applies to a
freshly regenerated tree with **57 of 58 files clean, zero fuzz, zero offset**.
The single failure is `C2184d.java`, where the generated *filename* itself
moved — that class is cited by the BLE mutual-auth finding, and its annotation
is reproduced in `findings/ANNOTATIONS.md` so nothing is lost.

For citations: a class path plus a real member name survives regeneration; a
reference to `f34717r` or to `C2184d.java` does not. `cyncdec` resolves both
spellings, so `read C2184d` and `read d` both work.

## Layout

| | |
|---|---|
| `apply.sh` | Regenerate, verify, apply. |
| `patches/` | The annotation patch — 58 files, ~1,450 added lines. |
| `findings/` | Standalone analysis: native libraries, the Matter/CHIP dead-weight finding, the camera subsystem, XlinkDTSL, the extracted command catalogue, the ge-sdk package map, and every inline annotation as prose. |
| `tools/` | `cyncdec` — hex-annotates byte literals, recovers Kotlin and enum names, extracts the command catalogue, walks the reference graph. 98 tests. |

`tools/` is worth having whether or not you ever regenerate the tree; it
resolves all 105 command opcodes on its own.

## Where the conclusions live

This branch is the evidence. The conclusions drawn from it are in `docs/` on
the release branches — `mesh_opcodes.md`, `packet_structure.md`,
`cync_automations.md`, `ble_provisioning_protocol.md` — and those carry
confidence markers that matter more than anything here.
