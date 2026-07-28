# Contributing

The single most useful thing you can contribute is **a report from real
hardware**. Read on for why that is not a polite deflection.

## The state of this project, honestly

Of the 27 experimental commands the integration implements, **one** —
`set_indicator_led` — has been confirmed working on real hardware. The other 26
were reconstructed from the decompiled Cync Android app, and their outer
envelope byte (`cmd_code`) is *predicted from a length formula* rather than
observed on the wire.

Nobody owns every Cync device. Support for a model generally arrives because
someone with that model captured what it does. That is the bottleneck, not
code.

So: if you turn on experimental commands and something works — or silently does
nothing — that is a genuinely valuable data point, and there is nowhere else to
get it.

## Reporting a hardware result

Open a [discussion](https://github.com/Proxy-alt/cync-lan/discussions) for
"did this work", or an [issue](https://github.com/Proxy-alt/cync-lan/issues)
for something broken. Either way, include:

- **Device model**, as printed on the device or shown in the Cync app
- **What you ran** — which entity, action or button
- **What happened**, including "nothing at all". A silent no-op is a result, and
  it is the expected failure mode when an opcode is wrong
- **`experimental_features.log`**, which records every experimental invocation
  automatically. It sits alongside your other cync-lan files
- Debug logs if you have them (`cync_lan` at debug level prints outgoing packet
  hex). **Redact identifiers before pasting**

A negative result is worth as much as a positive one and takes the same effort
to report. "Tried the group power switch on a Direct Connect bulb, nothing
happened" closes a question that documentation cannot.

## The evidence standard

Protocol claims in `docs/` carry explicit confidence markers, and pull requests
touching them are expected to keep that up:

- **confirmed** — cited to an exact decompiled class and line, proven by a
  packet capture, or already shipping and working
- **plausible** — a reasonable inference, not proven. Say what would confirm it
- **not found** — say so explicitly rather than guessing

A guessed opcode that looks right is worse than a documented gap, because the
failure mode is a command that silently does nothing and sends the next person
debugging the wrong layer.

## Code changes

```bash
# Home Assistant integration (feature/ha-custom-component)
pytest tests/
mypy -p custom_components.cync_lan

# Protocol library (core)
pytest tests/
ruff check src/ tests/ scripts/
ruff format --check src/ tests/ scripts/
```

Run these before pushing. CI runs them too, but the release workflow gates on
tests alone — a lint failure will land in `main` and sit there, which has
happened.

A few things worth knowing before a first PR:

- **Three branches, three artifacts.** `core` is the protocol library on PyPI,
  `python` is the Docker/MQTT add-on, `feature/ha-custom-component` is the Home
  Assistant integration. Each versions and releases separately —
  see [RELEASING.md](./RELEASING.md).
- **`docs/` is mirrored byte-for-byte across all three**, canonical copy on
  `core`, and CI fails on drift. Edit it on `core` and copy to the others in
  the same change.
- **Setup guides live in the [wiki](https://github.com/Proxy-alt/cync-lan/wiki)**,
  not in `docs/`. Router UIs change and nobody owns every model, so those pages
  are editable directly — no PR needed.
- **New experimental commands stay behind the opt-in** until confirmed on
  hardware. That gate is the only thing between a user and an unproven mesh
  write.

## Two standing warnings

**Do not contact GE or Savant** about this project. They did not write it and
cannot support it.

**Do not firmware-upgrade Cync devices** once you are relying on local control.
It would be trivial for the vendor to disable this method.
