# `cyncdec` — tooling for reading this decompile

A decompiled Android app resists reading in four specific, mechanical ways.
Each tool here removes exactly one of them:

| The obstacle | What it looks like | Tool |
| --- | --- | --- |
| Protocol bytes print as signed decimals | `new byte[]{-46, 17, 2}` when the wire says `D2 11 02` | `read`, `opcodes` |
| R8 renamed every field and method | `f34717r`, `mo14392a` | `names`, `enums` |
| 85% of the tree is vendored SDKs | 24,300 classes, ~3,700 of them Cync's | `map`, `find`, `grep` |
| Following a flow means grepping in circles | tap slider → ??? → bytes on the wire | `xref`, `trace`, `process` |

Nothing here modifies `sources/`. Everything writes to stdout or a file you
name, so the tree stays byte-identical to the JADX output and stays diffable
against a future re-decompile.

## Setup

None. Python 3.10+, no dependencies.

```bash
tools/cyncdec.sh map
```

The wrapper works from any directory. For a shorter invocation:

```bash
alias cyncdec="$PWD/tools/cyncdec.sh"
```

The first command builds a class index (~0.3s) and caches it in `tools/.cache/`.
The enum map costs ~13s once, then caches too. Both refresh with `--refresh`,
which you want after re-running JADX.

## The commands

### Orientation

```bash
cyncdec map                 # app vs vendor, with file counts
cyncdec map --deep 20       # plus the biggest app packages
```

`map` also reports anything in the tree that `paths.py` does not classify —
so a package that appears in a future decompile shows up as **UNCLASSIFIED**
rather than being silently skipped by every other tool.

### Finding things

```bash
cyncdec find SetBrightness           # class name → FQN + path
cyncdec find devices.command         # partial FQN also works
cyncdec grep "0x73"                  # full-text, app packages only
cyncdec grep --scope all "tinyDTLS"  # include vendor when you mean to
cyncdec grep -e "byte\[\]\s*\{-?\d+" # regex
```

`--scope` also takes a path prefix, e.g.
`--scope com/gelighting/cbygekit/services/devices`.

### Reading a class

```bash
cyncdec read SetBrightnessCommand
```

Strips the `@Metadata` blobs and JADX bookkeeping comments, then adds back what
they were hiding: the recovered Kotlin names as a header, `// hex:` for every
byte literal, and `// WriteType.f34482b = NO_ACKNOWLEDGEMENT` for every enum
constant. A 122-line file becomes ~85 readable ones. `--raw` disables the
stripping, `--inline-hex` rewrites the literals in place instead of annotating.

### Recovering names

```bash
cyncdec names SetBrightnessCommand   # Kotlin metadata + enum constants
cyncdec enums Priority               # tree-wide, filtered
cyncdec enums | wc -l                # ~2,200 constants recovered
```

Two different mechanisms, with different reliability, and the tools keep them
separate on purpose:

- **Enum constants are exact.** Kotlin keeps the constant name as a string
  argument to the synthetic constructor, so `new WriteType("DEFAULT", 0)`
  binds the name to the field in the same static block. R8 cannot strip it.
- **Kotlin `@Metadata` names are a strong hint, not a proof.** The `d2` array
  is the class's declared names in source order, but binding them to specific
  obfuscated members needs the `d1` protobuf, which this does not decode. Use
  it to learn that a class *has* a constant called `OPCODE`; confirm which
  field that is by how the field is used.

### Following a flow

```bash
cyncdec xref SetBrightnessCommand              # one hop, both directions
cyncdec trace SetBrightnessCommand --depth 3   # the graph, as a tree
cyncdec trace SomeFragment -d up               # who reaches this
cyncdec process SetBrightnessCommand -o /tmp/brightness.md
```

`process` is the one that saves real time: it walks the graph and writes a
markdown report where each class already has its byte literals in hex, its
recovered names, its resolved enum constants, its method signatures, and any
`cync-lan reverse-engineering note` comments a previous pass left in the
source. That is close to the form a findings write-up needs anyway.

Generated Dagger/Hilt/databinding classes are pruned by default — without that,
walking up from anything injectable is 90% `_Factory` classes. `--no-skip`
turns the pruning off.

### The op-code namespace

```bash
cyncdec codes                # every XlinkCommandCode constant, by value
cyncdec codes --usage        # ...and which command class sends each
cyncdec codes hub            # filter by name or hex
```

`XlinkCommandCode` is the Xlink/hub command namespace. `--usage` cross-links it
against the extracted catalogue, which is how you spot **ops the protocol names
but no command class sends** - currently eight, of which `PASSTHROUGH_8E` and
`HUB_PASSTHROUGH_8C` are relay wrappers the delegate uses directly and
`DEVICE_STATUS` is a response op, leaving `HUB_EDIT_GROUP`, `SET_HUB_TIME`,
`HUB_TIME_QUERY`, `HUB_WIFI_CONFIGURE` and `HUB_CHECK_UPDATE` as capabilities
the firmware appears to accept but the app never exposes.

### Regenerating the command catalogue

```bash
cyncdec opcodes                      # markdown
cyncdec opcodes --table              # terse columns
cyncdec opcodes --json               # for scripting
cyncdec opcodes -o docs/COMMAND_CATALOGUE.md
```

## What these tools will and will not tell you

Stated plainly, because the whole point of this tree is evidence for protocol
claims, and a tool that quietly guesses is worse than no tool.

**Sound:**

- Hex conversion, enum constant names, class index, file classification.
- An opcode in the `telink`/`xlink` columns: the static array that *that
  transport's own send call* passes, resolved through symbolic constants, one
  payload-builder hop, the chunking helper, or a parent class. Inherited
  opcodes are labelled with the parent they came from.
- A `scalar_op`: the single byte handed to the Xlink frame builder. **Kept in a
  separate table from mesh opcodes on purpose** — a hub command's op byte is
  not a mesh opcode and carries no 7-byte routing prefix, so merging the two
  columns would hand an implementer the wrong wire shape.
- `op_variants`: where a command picks its op at runtime, the full candidate
  set is reported rather than one arbitrary member.

**Over-inclusive by design:**

- `xref`/`trace`/`process` edges are *references* — imports and same-package
  name mentions — not proven calls. A class named only in a type signature
  shows up as an edge. For RE, a missing edge costs more than a spurious one.

**Known blind spots:**

- Calls made through an interface do not link the caller to the implementation.
  `SetBrightnessGeCommandHandler` traces up to nothing but its Dagger factory,
  because whatever invokes it does so through a generic handler interface.
  Interface-to-implementation resolution is not implemented.
- Payloads assembled through a `ByteArrayOutputStream` are not decoded, so
  those commands show `-` for their opcode (`ExecuteSceneCommand` is the
  worked example — its two arrays are `EF 11 02` and `F0 11 02`, and only
  reading the method tells you which goes where).
- `@Metadata` `d1` is not decoded; see above.

## `findings/`

Hand-written research output, not generated. Three passes done 2026-07-25 over
the commands the extractor could not resolve:

| File | Covers |
| --- | --- |
| `multipart_commands.md` | The `sendBlocks` chunking scheme and the 11 commands using it |
| `query_commands.md` | The shared query base, request/response envelopes, `0xEA` subtype table |
| `hub_commands.md` | The WiFi-bridge family: opcodes, HDLC framing, payload layouts |

Those passes found several bugs in this toolkit, all since fixed and covered by
tests. Coverage went from **41 to 105 of 105** real command classes as a result,
and every value the tool derives matches what the manual passes established
independently (13/13 mesh opcodes, 17/17 hub ops).

The gaps that had to close, each a distinct way an opcode can hide:

| Gap | Fix |
| --- | --- |
| R8 merged the literal `16` into BouncyCastle's `Tnaf.POW_2_WIDTH`, hiding 16 arrays behind one symbolic element | `consts.py` |
| Chunked sends reach the opcode through a shared builder, never touching the delegate | `_method_body_with_param` |
| JADX writes field arrays both as `new byte[]{…}` and bare `{…}` | widened `_BYTE_ARRAY_ASSIGN` |
| Hub commands pass a scalar op to a frame builder, sometimes as a named enum constant | `_scalar_op_in` + `command_code_table` |
| Subclasses that override only parameters inherit the parent's send | `_inherit_opcodes` |
| One command selects its op at runtime from two candidates | `op_variants` |

The 106th class, `IgnoreResultDeviceCommand`, is a decorator that forwards to a
wrapped command — it has no opcode by construction and is classified `wrapper`,
not counted as an unresolved command.

## Layout

```
tools/
  cyncdec.sh          wrapper: sets PYTHONPATH, runs the module
  test_cyncdec.py     42 smoke tests - run this after any change
  cyncdec/
    paths.py          app vs vendor classification; edit when the tree changes
    index.py          class index, caching, scoped full-text search
    hexify.py         signed-decimal → hex
    kmeta.py          Kotlin @Metadata name tables
    enums.py          enum constant recovery (the exact one)
    render.py         the `read` view
    opcodes.py        command catalogue extraction
    trace.py          reference graph, tree rendering, process reports
    cli.py            argument parsing
```

`paths.py` is the file to edit when a re-decompile shifts things around: it
holds the app/vendor lists that every other tool scopes by. `cyncdec map` tells
you when it has gone stale.

## Tests

```bash
python3 tools/test_cyncdec.py
```

The pure tests use inline fixtures. The rest assert facts about *this*
decompile — `SetBrightnessCommand` carries `D2 11 02`, `SetFanSpeedCommand`
sends a different opcode per transport — so they fail loudly if the tree is
replaced with a different build, which is the point.
