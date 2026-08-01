<!--
Thanks for contributing. Most of this is optional - delete whatever does not
apply. The one section worth filling in properly is the protocol one, if you
touched protocol code or docs.
-->

## What this changes

<!-- One or two sentences. What is different afterwards? -->

## Which artifact

<!-- Three branches, three separately released things. Tick what this targets. -->

- [ ] Home Assistant integration (`feature/ha-custom-component`)
- [ ] Protocol library (`core`)
- [ ] Docker/MQTT add-on (`python`)
- [ ] Documentation only

## Checks

- [ ] Tests pass (`pytest tests/`)
- [ ] Types pass — `mypy -p custom_components.cync_lan`
- [ ] If `docs/` changed, the same change is in all three artifact
      repositories — CI compares against `cync-lan-lib` and fails on drift

## Protocol changes

<!-- Delete this section if you did not touch opcodes, payloads or docs/. -->

**Confidence:** <!-- confirmed / plausible / not found -->

**Evidence:** <!-- A decompiled class and line, a packet capture, or "worked on
my <model>". "Seems right" is not evidence - a wrong opcode fails silently, so
an honest "plausible" is worth more than an optimistic "confirmed". -->

**Tested on hardware?** <!-- Which device model, and what happened - including
"nothing happened", which is a real result. -->

## Anything you are unsure about

<!-- Genuinely useful. Half this project's protocol knowledge came from someone
saying "this bit looks wrong but I could not prove it". -->
