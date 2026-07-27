# Regenerating the decompiled Cync app

Most of this project's protocol documentation cites the decompiled Android
app. The tree itself is not in this repository — it is ~205 MB of Savant's
compiled code, and redistributing it is a different thing from documenting
what the protocol does. This directory holds everything needed to recreate it,
plus the analysis that came out of it.

## The recipe

```bash
jadx --comments-level debug --no-inline-anonymous --deobf -d out/ cync.apk
```

| | |
|---|---|
| APK | `com.ge.cbyge`, 175,320,244 bytes |
| SHA-256 | `3d57c08b5c180bf7872c91186a78dc60e2684fb92796feb78ac9163bf33ec06c` |
| jadx | 1.5.6 verified; see "version" below |
| Output | 55,368 `.java` files |

## Why these flags

The flags were not recorded when the tree was made and were reconstructed by
experiment, comparing regenerated output against the committed baseline.

**`--comments-level debug` is the one that matters.** Without it jadx silently
drops the body of any method it fails to decompile, leaving only
`/* Method dump skipped, instruction units count: 653 */`. That affected **254
files**. It is the entire reason a v2 decompile exists: the four commissioning
methods in `BleDeviceCommissionService` that the BLE provisioning research
could not read are skipped without it, and present with it. jadx prints the
remedy itself, in the skip comment: *"To view this dump add
'--comments-level debug' option"*.

**`--no-inline-anonymous`** brings the count of methods jadx emits as raw
fallback from 19 down to 10, matching the baseline.

**`--deobf`** is easy to rule out incorrectly, and was — twice, by different
reasoning. Class names like `SetBrightnessCommand` survive it, so the tree
looks un-deobfuscated. But `--deobf-min` defaults to 3, so deobfuscation only
renames identifiers *shorter* than three characters. That is where the
`C2551Ok` / `m28775d1` / `f31596n` names come from: `Ok`, `d1` and `n` are all
under the limit. If a regenerated tree has plain `n` and `Ok`, this flag is
missing.

Ruled out by experiment, with no effect on the output: `--no-imports`,
`--no-replace-consts` (leaving it off is *why* `Tnaf.POW_2_WIDTH` appears
instead of a literal `16`), `--show-bad-code`, `--no-inline-methods`
(249 bytes), and raising the JVM thread stack with `-Xss` up to 64 MB — the
`JadxOverflowException: Regions stack size limit reached` in the logs is not
fixed by more stack.

## What reproduces, and what does not

**Reproduces exactly:** the class set (55,368 files, matching the baseline
file-for-file), which methods decompile and which fall back, and every real
identifier — class names, method names, field names that were longer than
three characters before obfuscation.

**Does not reproduce:** jadx's *generated* identifiers. Running the identical
command on the identical APK twice produces `C2551Ok` one time and `C3120Ok`
the next, `f31596n` and then `f31617n`. The counter is assigned across the
whole input and is not stable between runs.

That has a practical consequence for citations. A reference like
`SetBrightnessCommand.java`'s `OPCODE_BYTES` survives regeneration. A reference
to `f34717r`, or to the file `C2184d.java` (which the BLE mutual-auth finding
cites), does not — that class is one of two annotated files whose *path* moved
between runs.

Prefer citing a class path and a real member name. Where an obfuscated name is
unavoidable, say what it is next to, so the next person can find it again.

### Checking a regenerated tree

```bash
grep -rl "Method dump skipped" out/sources/ | wc -l     # must be 0
grep -rl "Tnaf.POW_2_WIDTH" out/sources/ | wc -l        # must be > 0
find out/sources -name '*.java' | wc -l                 # 55368
```

Zero skips means `--comments-level debug` took effect; the `Tnaf` constant
means const replacement was left on; the file count confirms the input matches.

### On the jadx version

1.5.6 is verified. 1.5.5, 1.5.0 and 1.4.7 were each tested and land *further*
from the baseline, so the version is not what distinguishes this tree — the
missing `--deobf` was. Any 1.5.x should satisfy the checks above, but only
1.5.6 has been confirmed against the baseline.

## What is here

| | |
|---|---|
| `annotations.md` | The 98 inline `[cync-lan reverse-engineering note …]` comments across 61 files, as a list rather than a patch — patch context would carry unstable generated identifiers. |
| `findings/` | Standalone analysis: native libraries, the Matter/CHIP dead-weight finding, the camera subsystem, XlinkDTSL, the extracted command catalogue, and the ge-sdk package map. |
| `tools/` | `cyncdec` — reads a decompiled tree: hex-annotates byte literals, recovers Kotlin and enum names, extracts the command catalogue, walks the reference graph. Run `tools/cyncdec.sh map` to start. |

`tools/` is the part worth keeping regardless of whether anyone regenerates
the tree; it resolves all 105 command opcodes and has its own tests.
