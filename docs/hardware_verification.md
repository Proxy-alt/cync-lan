# Hardware verification status

Of the 27 experimental commands this project implements, **two** have been
confirmed against real hardware: `set_indicator_led` and `identify`. The other
25 were built from decompiled source with a `cmd_code` predicted by the length
formula in [mesh_opcodes.md](mesh_opcodes.md), and have never been observed
working.

That is not 26 independent unknowns. Every command travels one of three
dispatch families, and a single successful test tells you about its whole
family. **Four tests would resolve the status of all 26.**

## The four questions

| # | Question | Cheapest test | Resolves |
|---|---|---|---|
| 1 | ~~Is the `0x8E` mesh-relay envelope right?~~ | **Answered: yes.** Identify confirmed on an outlet | see Family 1 |
| 2 | Is the hub-command envelope right? | **Delete scene**, or **Sync hub clock** | 11 commands |
| 3 | Do query replies come back over the TCP relay at all? | **Hub clock** sensor | 5 queries |
| 4 | Does the `0xE2` sub-command form work? | A light **transition** | 2 commands |

Question 2 deserves emphasis: **no** hub-family command has ever been
confirmed, and until cync-lan 0.2.0 all six of the original ones sent a length
field one byte short - so they were definitely broken, and are now only
*probably* right. If you test one thing beyond Identify, test this family.

Question 3 is the one that could invalidate a whole design direction. Every
query command depends on `_await_xlink_notification`, and there is no evidence
the reply channel rides the transport this project intercepts. If replies
never arrive, no further read-back commands are worth building.

## Run them in this order

Ordered by information gained per minute, with the branch to take when a step
fails. Stop whenever you run out of patience - the value drops off sharply
after step 3.

### Step 0 — Establish a baseline (5 min, do not skip)

Turn a light on and off from Home Assistant. Check the bridge's **Connected
devices** reads more than zero, and that the device you plan to test shows
**Ready to control** as on.

Without this, every later result is uninterpretable: "the command did nothing"
and "that device was never reachable" look identical. Also turn on debug
logging now - Settings → Devices & services → Cync LAN → Enable debug logging,
or set `custom_components.cync_lan` and `cync_lan` to `debug` in
`configuration.yaml` - so every later step records the bytes it actually sent.

**If basic on/off does not work, stop.** Nothing below will mean anything.

### Step 1 — Identify — **done, it works**

Confirmed on an **outlet**. The `0x8E` envelope is right, on a second device
class and a second sub-code, so Family 1's framing is no longer the open
question - see that section.

Kept here because it is still the right first step for anyone verifying their
own hardware, and because the result should be reproduced rather than taken on
one report: a visible physical effect, on hardware you already have, from a
command that cannot damage anything.

What it physically does is still worth writing down if you run it. "Announce
itself" was inferred from the class name, and one confirmation that *something*
happens is not a description of what.

- **Works** → matches the existing result; go to step 2.
- **Nothing** → the predicted `cmd_code` is wrong for the whole family,
  including the entities that already appear to work. Skip to step 3, which
  tests a different family, before concluding anything.

### Step 2 — Hub clock, read only (5 min) → up to 16 commands

Enable the **Hub clock** sensor on the bridge (it ships disabled), wait for a
poll, and see whether it reports a plausible date and time.

The highest ceiling of any single test here, because a hub query is a
hub-family command that also has to get an answer back. A real reading proves
**both** that the hub envelope is right *and* that the reply channel rides
this transport - 11 commands plus 5 queries at once.

Its weakness is that failure is ambiguous, which is what step 3 is for.

- **Reads a sensible time** → families 2 and 4 are both credible. You are
  done with the structural questions; the rest is detail.
- **Stays unavailable** → either the hub envelope is wrong or replies never
  arrive. Go to step 3 to tell those apart.

### Step 3 — Delete a scene (5 min) → disambiguates step 2

Only needed if step 2 failed. Requires a scene you do not mind losing;
create a throwaway one in the Cync app first.

Enable the **Delete scene** button for it (destructive buttons ship disabled),
press it, then check the Cync phone app.

This is a hub-family **write** with an externally visible outcome, so it
isolates the two failure modes step 2 conflates.

- **Scene disappears** → the hub envelope is right; the *reply channel* is
  what is broken. Query commands cannot work over this transport, and no
  further read-back commands should be built.
- **Scene remains** → the hub envelope itself is wrong. All 11 hub commands
  are suspect, including the six whose length field was corrected in 0.2.0.

### Step 4 — Dimmer LED bar and brightness (5 min)

On a dimmer switch, set **Dimmer LED bar** to each of its two values, then
move **Dimmer LED brightness** between roughly 20 and 100.

Detail rather than structure, but it tests one thing nothing else does: the
brightness change sends two packets, Preview then Save. If the bar changes
while you drag and then snaps back, the Save byte is wrong - a distinct
failure worth reporting precisely.

### Step 5 — A light transition (2 min) → 2 commands

Call `light.turn_on` on a dimmable light with a `transition:` of a few
seconds. This is the only route to `set_fine_brightness` and the only test of
the `0xE2` sub-command family.

- **Fades** → that family works.
- **Jumps straight to the level** → the transition was ignored; `0xE2/0x08`
  is wrong.

### Step 6 — Motion-sensor settings (10 min)

**Hold the sensor's off button for about 5 seconds until its LED turns green
first.** The real app requires this before it will accept a settings write,
and without it the app itself reports success without transmitting - so a
result recorded without the wake step means nothing.

Then use Configure → Experimental commands → *Write a motion-sensor schedule
slot*. Worth noting separately whether the wake requirement also applies to
dimmer/motion combination switches, or only to standalone sensors.

### Step 7 — Sync hub clock (2 min)

Only meaningful if step 2 read a value. Press **Sync hub clock**, wait for the
next poll, and see whether the reading moved to your Home Assistant time.

## Family 1 — `0x8E` mesh-relay

**Two confirmed members, on two different device classes.** `set_indicator_led`
was confirmed on a switch; `identify` has since been confirmed on an **outlet**.

The second confirmation is worth more than a second tick. One member working
could have meant that member's payload happened to be right; two members with
different sub-codes (`0x06` and `0x03`), on different device classes, working
through the same `XlinkCommandDelegate.DefaultImpls.c()` → `h()` dispatch, is
evidence about the **envelope** rather than about either command. That was the
open question this family existed to answer.

It does not confirm the remaining ten individually. Each still carries its own
sub-code and its own payload layout, and a wrong payload inside a right
envelope fails silently the same way. What has changed is that a failure in
this family is now most likely that command's own bytes rather than the
framing everything shares.

| Command | Sub | Needs | Status |
|---|---|---|---|
| `set_indicator_led` | `0x06` | any switch | **CONFIRMED** |
| `identify` | `0x03` | any device | **CONFIRMED** (outlet) |
| `set_dimmer_led_mode` | `0x62` | dimmer switch | untested |
| `set_dimmer_led_brightness` | `0x63` | dimmer switch | untested |
| `set_motion_sensor_settings` | `0x07` | motion sensor | untested |
| `set_motion_sensor_schedule` | `0x0B` | motion sensor | untested |
| `set_multicolor_gradient_mode` | `0x4E` | RGB strip | untested |
| `set_multicolor_segment_count` | `0x4E` | RGB strip | untested |
| `set_multicolor_segments` | `0x4E` | RGB strip | untested |
| `execute_scene` | - | a saved scene | untested |
| `add_to_scene` / `remove_from_scene` | - | a saved scene | untested |
| `set_group_membership` | - | a group | untested |

## Family 2 — hub commands

**Zero confirmed.** All six of the original members had a malformed length
field until cync-lan 0.2.0.

| Command | op | Needs | Note |
|---|---|---|---|
| `create_scene` / `delete_scene` | `0x10` / `0x1F` | a saved scene | length fixed in 0.2.0 |
| `create_schedule` / `delete_schedule` | `0x92` / `0x94` | a saved schedule | length fixed in 0.2.0 |
| `add_automation` / `toggle_automation` | `0x95` / `0x93` | a saved schedule | length fixed in 0.2.0 |
| `delete_automation` | `0x97` | a saved schedule | new in 0.3.0 |
| `delete_group` | `0x32` | a group | new in 0.3.0 |
| `set_group_power` | `0xD0` | a group | group-addressing unconfirmed |
| `set_time` | `0x40` | any hub | new in 0.4.0 |

## Family 3 — classic op families

| Command | op | Needs |
|---|---|---|
| `set_fine_brightness` | `0xE2/0x08` | dimmable light |
| `set_light_effect` / `set_lightshow` | `0xE2/0x07` | RGB light |

## Family 4 — query replies

All of these send fine; what is unproven is whether anything comes **back**.

`query_hub_info` (`0x4B`), `query_device_time` (`0x46`),
`query_sol_config` (`0xAD`), `query_hub_mesh_credentials` (`0x8A`),
plus `create_scene`/`create_schedule`, which read back an allocated id.

## Recording a result

A command that appears to do nothing is a useful result, not a failed test -
it says the predicted envelope for that family is wrong. When recording one,
note the device model, what you pressed, and what did or did not happen. Debug
logs help: set `custom_components.cync_lan` and `cync_lan` to debug, and the
outgoing packet hex is logged for every command.
