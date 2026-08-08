# Changelog

Version history for the Home Assistant `cync_lan` custom_component
specifically (`custom_components/cync_lan/manifest.json`'s `version` field).
This is independent of the [`cync-lan`](https://github.com/Proxy-alt/cync-lan-lib)
core protocol library's own version scheme and the
[`cync-lan-mqtt`](https://github.com/Proxy-alt/cync-lan-mqtt)
Docker/MQTT add-on's own version scheme - all three are versioned and
released separately, even though this integration depends on `cync-lan` to
do the actual protocol work.

### 2.11.0

**Requires cync-lan 0.11.0.** No behaviour changes here - this is the
integration side of a library change that makes it safe to install `cync_lan`
and `cync_ble` together.

The library's cloud client used to be a process-wide singleton, so
`CyncCloudAPI()` anywhere in Home Assistant returned whichever integration
built one first, along with its token and its config directory. That is
invisible with one integration installed and quietly wrong with two: the
second one authenticated as the first one's account, wrote to the first one's
directory, and reported the resulting token failure to its own user as "could
not reach the Cync cloud API."

This integration genuinely does want one client per process - the firmware
sensor reads what the server's periodic check captured - so both call sites
now ask for it by name via `CyncCloudAPI.shared()` instead of relying on
construction to do it silently. The version floor is a hard requirement
rather than a preference: `shared()` does not exist in 0.10.4, so an install
that somehow kept the older library would fail at the call rather than
misbehave.

Nothing to do on upgrade, and nothing changes about how this integration
behaves on its own.

### 2.10.3

**Requires cync-lan 0.10.4**, which fixes a third way cloud passthrough went
quiet - this one after 2.10.2 was supposed to have settled it, and only after a
device reconnects to the cloud.

The library flag that means "relay but keep controlling" was cleared whenever
the proxy stopped, leaving the flag that means "relay and stay silent" set. The
reconnect path and the idle-connection watcher both stop and restart the proxy
as ordinary business, so a session went silent on its first cloud reconnect and
stayed that way until Home Assistant restarted.

Unlike the 2.9.0 version of this bug, it does not switch the house off. Commands
go out over two randomly chosen device connections, so one is lost only when
both draws happen to be silent ones. On the install this was found on, 21 of 46
connections were affected, which is about one command in five doing nothing -
close enough to flaky hardware or a weak mesh to be worth saying plainly: if
you have cloud passthrough on and have been blaming your lights, this was
probably us.

The version floor is why this release exists. 0.10.4 fixes it, but a Home
Assistant install that already has 0.10.3 satisfies the old `>=0.10.3`
requirement and never upgrades on its own.

### 2.10.2

**Requires cync-lan 0.10.3**, which fixes two ways cloud passthrough broke
devices. 2.9.0 shipped the option reusing the library's `mitm_mode` flag, and
that flag means "relay to the cloud *and* stay off the wire" - correct for the
per-device capture switch it was written for, wrong for an option whose whole
point is to keep working. Reported from a live install where nothing could be
switched on while it was enabled. If you have passthrough on, update; until you
do, turning the option off restores control immediately.

**The e2e suite now runs every option combination.** All 256 products of the
eight boolean options set the entry up against a real server and assert the
entities exist and the entry unloads - about two minutes, which is the cost of
noticing that a single flag changes whether the integration works at all.

Being straight about what that matrix does and does not do: it asserts setup
and entities, so it would **not** have caught this bug. The test that does is
`test_a_command_reaches_the_device_with_passthrough_either_way`, which turns
the option on and then switches a light. Verified by mutation - revert the
library fix and that case fails while all 256 matrix cases stay green. Breadth
and depth are different tests and it is worth not confusing them.

**Malformed options are covered too**, because options are not always what the
options flow wrote - a downgrade after a newer version stored a key, a
hand-edited `.storage`, a schema change between releases. Nine cases: missing
options entirely, a string and a `None` where a boolean belongs, negative and
zero intervals, out-of-range ports, an unknown key from the future. The
contract is narrow on purpose: set up, or fail in a way Home Assistant
understands. An arbitrary exception escaping setup is what leaves an entry
wedged with no entities and a traceback nobody can act on.

**The matrix found a real bug on its first outing**, and not the kind it was
aimed at. With passthrough on, an unwritable capture-log directory made the
library refuse *every* device connection - `mkdir` on a path frozen at import
to `/root/cync-lan/config`, an `OSError` escaping through `start_tasks`, and
`_register_new_connection` swallowing it without registering the session. That
is the state a Home Assistant container is actually in. Fixed in cync-lan
0.10.3; the matrix surfaced it because running any test file before the e2e
file leaves that import-time default in place.

**Tests can no longer dial the vendor.** `CYNC_CLOUD_IP` defaults to the real
Cync address, so any test enabling passthrough would have opened a connection
to GE from CI. The fixture now pins the cloud to a dead local port, and tests
wanting a working relay point at a `FakeCloud` instead.

### 2.10.1

**An OTP starting with `0` could never complete setup.** The flow collects the
code as a string and then cast it with `int()` before handing it over, so
`012345` went out as `12345`; the vendor rejected five digits as invalid, and
retyping the same correct code failed identically every time with nothing to
suggest why. Roughly one code in ten starts with a zero.

Found while merging @baudneo's
[cync-lan-lib#1](https://github.com/Proxy-alt/cync-lan-lib/pull/1) - he flagged
the same bug in the library, and fixing it there (0.10.1, which this now
requires) only helps if this end stops destroying the zero first. Both halves
were needed.

While covering it, `test_otp_step_cannot_connect` turned out to assert
`invalid_otp` - the opposite of its own name. It passed only because
`int("not-a-number")` raised `ValueError` before `send_otp` was ever reached,
so the mocked error it exists to test was never exercised. Corrected, and the
`invalid_otp` path it had been accidentally covering now has a test of its own.

`requirements_test.txt` tracks the manifest exactly again.

### 2.10.0

**End-to-end tests through the whole stack**, in `test_e2e_device.py`: a real
`nCyncServer` on an ephemeral port, real platform setup so entities are
genuinely created, and a real Cync device connected over TLS - the library's
own `cync_lan.testing.VirtualCyncDevice`, shipped in 0.10.0 so both
repositories drive the same simulator instead of two copies that drift.

Every other test here mocks `nCyncServer`, which is right for what they check
but leaves the seam this integration actually *is* - Home Assistant's entity
model on one side, a device on a socket on the other - with nothing covering
it. In between sit the config parse, entity construction, the bridge,
`send_command`'s framing and the broadcast pool.

`light.turn_on` now has a test that reads what arrives on the socket and
asserts the `0x73` control packet carries `11 02 01 00 00`, the payload
`set_power` builds. Verified by mutation: make `set_power` ignore its state
argument and the turn-on test fails while turn-off still passes, which is the
discrimination that makes it worth having.

One thing worth knowing if you write more of these: setup goes through
`hass.config_entries.async_setup()` rather than calling `async_setup_entry()`
directly, as the rest of the suite does. A direct call leaves the entry in
`NOT_LOADED`, `async_forward_entry_setups` refuses to run from that state, and
no entities are ever created - so a test written the usual way silently checks
nothing.

**What this does not do** is tell you a bulb turns on. The simulator is built
from the library's own understanding of the protocol, so a pass means the stack
agrees with itself. `docs/hardware_verification.md` is still the only record of
what hardware has confirmed.

### 2.9.1

**Requires cync-lan 0.9.2.** 2.9.0 shipped cloud passthrough against a library
release with two bugs in that exact path, both found by new end-to-end tests
that drive the server over a real socket rather than a mocked one:

- `stop_proxy()` caught `Exception` around an `await` on a task it had just
  cancelled, and `asyncio.CancelledError` has not been an `Exception` since
  Python 3.8. It raised straight back out and skipped the rest of its own
  teardown - the cloud connection stayed open, the watcher kept running, and
  the caller's shutdown was cancelled with it.
- `start_proxy()` read `self.node.id` to name a task, before the device has
  identified itself. Because it was only a name, the failure read as "failed
  to start MITM" and quietly fell back to local-only.

No change to this integration's own code. If you have passthrough switched on,
this is the version to be on.

### 2.9.0

**New: cloud passthrough**, in Configure -> General settings. Every device
session this server accepts is relayed on to the real Cync cloud from its
first byte, while the integration goes on parsing that same traffic and
controlling devices locally. Devices stay cloud-connected, so the vendor's
app, its schedules and its firmware delivery keep working through a server
that is also doing its own thing with everything it sees.

**Understand what you are turning on.** With DNS redirection in place, this
integration is normally the reason your devices never talk to the vendor
again. This option deliberately undoes that: your device traffic - and, if
the Cync app connects through this server too, your app traffic - goes to
the vendor. That is the entire feature, not a side effect. Off by default,
and the label in the options form says so rather than hiding it in a doc.

Why you might want it anyway: firmware updates only arrive over the cloud
connection, the Cync app stops being able to change anything once devices
are redirected, and a passthrough is the only way to watch the vendor's own
traffic against your own hardware - which is how most of the protocol in
this repository was worked out in the first place.

The relay is the same machinery behind the per-device "MITM mode" switch
that has shipped disabled-by-default for a while. What was missing was a way
to say "all of it", and a way to start relaying without the forced reconnect
that switch needs - a session enabled at accept time has not read a byte
yet, so the cloud sees the handshake from the beginning. See the library's
0.9.0 notes.

A cloud that cannot be reached is not fatal: the session logs and carries on
in ordinary local-only mode.

**Requires cync-lan 0.9.1 or newer.** Older releases ignore the variable
this writes, which would look identical to the cloud refusing your devices,
so the options flow checks and warns rather than letting that happen
silently - the same guard the hub-envelope toggle uses. (0.9.0 carries the
feature; 0.9.1 adds the type annotations this repository's `mypy` job needs,
and `requirements_test.txt` moves with it - it had been pinned at 0.4.0
while the manifest asked for far newer, so CI was exercising a library the
integration declared it could not work with.)

### 2.8.0

**New: the indicator ring as a light entity** — the thing
`docs/ha_integration_architecture_and_uiux.md` §3 has described in detail,
including a sequence diagram, since before it existed. It did not exist:
`light.py` had no indicator entity and there was no Euclidean snapping anywhere
in the integration. Now there is.

The point is reach, not capability. The select/number/switch trio already sets
everything this does, but none of them can go on a light card, and none are
exposed to HomeKit or Alexa as a light. This is — so "set the porch switch ring
to red" works from anywhere that speaks lights.

**Enabled by a setting, and exclusive with the trio.** Turn on "Show the
indicator ring as a light instead of separate controls" and the light appears
while the select/number/switch entities stand down; turn it off and they come
back. Not both at once: all of them write the same *single atomic* mesh command,
so shipping both would put two UIs in a race over one piece of hardware and let
them disagree about its state. Whichever form is not in use is removed from the
entity registry, so flipping the option does not strand the other set as
permanently-unavailable entities.

Two lossy edges, both deliberate and both documented in the entity:

- **Colour is snapped.** The hardware takes an enum of four colours
  (`DimmingLedsIndicatorColor`), so an arbitrary RGB is mapped to the nearest by
  Euclidean distance. The entity then reports the *reference* RGB back rather
  than what was requested — reporting the request would claim a precision the
  device does not have. Not perceptually uniform, and with four widely separated
  points CIEDE2000 would not change a single result.
- **On/off maps onto mode**, which has three values. Off is `always_off`, on is
  `always_on` — *except* when the mode is already `normal`, which is on in every
  sense that matters. Forcing `always_on` there would silently undo a setting
  chosen from the mode select, so a ring already in `normal` is left alone.

`assumed_state`, like its siblings: the device never reports this back.

### 2.7.0

**New: a "Last firmware released" sensor, for people who turn on firmware
capture.**

The wait is the hard part. GE publishes rarely, so `CYNC_FIRMWARE_CAPTURE_DIR`
can sit idle for months — and then an image lands, and it is the most valuable
single artefact this project can get hold of: something real to inspect for
whether it is signed or encrypted, and whether an ESPHome/LibreTiny path is
conceivable at all. Nobody notices a file appearing in a directory. An entity
that changes state can drive an automation.

The state is the **target version**, so it changes exactly once per release.
Everything else is in the attributes: where the image was written, its size,
the source URL, and whether it matched the MD5 and size the cloud advertised. A
mismatch is surfaced rather than hidden — a truncated or re-signed image is
itself a finding.

**Only created when capture is switched on.** An entity reading "unknown"
indefinitely would be clutter for the overwhelming majority who never enable
it. Diagnostic category.

Nothing here installs anything. The sensor reads what the capture watcher
recorded, and the capture path has no route to a device — see
`cync_lan.cloud_api.capture_firmware`, whose signature takes an upgrade task
and a directory and nothing else. Requires `cync-lan` 0.8.0.

### Unreleased — documentation retraction, no code change

No component code changed here, so there is no version bump. This is recorded
because it removes a capability the documentation claimed this integration had.

**There is no local UDP transport, and there never was.** `docs/` listed
"Direct Local UDP (Port 5987)" as a supported pathway at "Native / Core Ready"
confidence with zero-config DHCP discovery, and the Core submission strategy
was built on it as a zero-DNS onboarding tier. Both were wrong.

The protocol is genuinely defined in the vendor SDK bundled in the Cync Android
app — discovery, an `MD5(access_key)` handshake, datapoint writes, pipe,
keep-alive, all on `XlinkProperty.DEVICE_PORT = 5987`. The firmware does not
implement it. All 46 Wi-Fi devices on the development account return ICMP port
unreachable on UDP 5987, and a 144-pair sweep of the surrounding ranges found
no UDP listener at all.

The app's own dispatch agrees: `XlinkAgent.sendPipeData` races a local UDP scan
against a cloud probe on every connect and takes whichever answers, and on this
hardware the local attempt can never win. Every device ends up `cloud control`,
which is the branch cync-lan intercepts.

**Consequence: TCP interception remains the only local pathway to Wi-Fi
devices, so the DNS-redirection requirement stands.** `cync-ble` — not a UDP
tier — is the sibling with no such requirement.

Scoped honestly: all 46 devices tested were provisioned and in service.
`HubManager.setWifiCredentials` drives commissioning through the same UDP scan,
so the port may well be open in setup state. That would not yield a control
transport, but it would make the accurate claim "absent after commissioning"
rather than "absent".

Full protocol map and method:
`cync-lan-research/findings/xlink_local_udp_absent.md`.

Two smaller corrections in the same pass: the feature matrix listed the Light
Show engine as partially implemented when all five run modes have been wired
for some time, and the Hexagon Tile layout row now carries the full wire format
(opcode, dispatch path, payload, and the chunked `sendBlocks` framing that is
the actual blocker) instead of a gesture at the source class.

### 2.6.3

Fixes the hub query sensors ("Hub Firmware", "Hub Clock") polling the mesh far
harder than intended.

These are diagnostic entities, disabled by default, and their class docstring
described the poll interval as "deliberately long". It was never actually set:
the class asked Home Assistant to poll it and no `SCAN_INTERVAL` existed
anywhere in the platform, so they ran on HA's 30-second default.

That matters because each poll puts a real command on the mesh and then blocks
for up to 10 seconds waiting for a reply. This command family's transport is
unconfirmed (see `docs/mesh_opcodes.md`), so on hardware where it simply does
not answer, every poll cost a timeout warning plus Home Assistant's own
"Update of sensor ... is taking over 10 seconds" - measured on a real system at
around 5,700 log lines a day, from a sensor that had never once produced a
value.

They now run on their own 15-minute timer rather than HA's polling, so the
other sensors on the platform - all cheap local reads - stay responsive.
Neither firmware version nor hub clock drifts meaningfully in that window.

If you enabled either of these and saw nothing but timeouts, that behaviour
itself is unchanged: the command may not be answerable on your hub. It is just
quiet about it now. The matching core-library fix stops the repeated warning
and stops waiting out the full timeout when there was no connection to send
the request on in the first place - relevant right after a restart, when Cync
devices can take many minutes to reconnect.

### 2.6.2

Reworks the `logo` variants added in 2.6.1. The LAN badge is now a smaller pill
tucked into the bottom-right, overlapping the tail of the wordmark, rather than
a full-size pill sitting beside it. Same idea as the icon's corner badge, so the
two read as one family.

### 2.6.1

Adds the wide `logo` brand variants, light and dark, alongside the square icons
that shipped in 2.4.x. Home Assistant uses the logo where a horizontal lockup
fits better than a square tile.

The icon takes its LAN badge as a corner overlay, which works because the mark
fills the tile. The same treatment on the wordmark lands on top of the final
"c" — it reads "Cyn" plus a sticker — so here the badge sits beside the
wordmark instead, which reads as "Cync LAN", the integration's actual name.

### 2.6.0

**The indicator LED is no longer experimental.** It has been confirmed working
on real hardware, so it is out from behind the opt-in.

The Select, Number and Switch entities for indicator-LED mode, colour,
brightness and blink-on-disconnect already appeared by default - that part was
already right. What changed is the action: it is now `cync_lan.set_indicator_led`
rather than `cync_lan.experimental_set_indicator_led`, and it is registered
whether or not experimental commands are enabled.

**The old name still works.** It was the only name for several releases, so it
is in people's automations, and renaming without an alias would break them
silently - the symptom being a light that does not respond, which nobody traces
back to a service rename. Calling the old name logs a warning asking you to
update, and otherwise behaves identically.

Everything else stays behind the opt-in. Of the 27 experimental commands this
integration implements, this is the only one confirmed against real hardware;
the other 26 still send a `cmd_code` predicted from a length formula, which is
exactly what the gate is there to protect people from.

### 2.5.3

Documentation only - no code change.

The comparison table on the repository's front page still said this integration
"is not on the default branch yet, which makes HACS installation more manual for
now". That stopped being true when `feature/ha-custom-component` became the
default branch; HACS installs work like any other custom repository. The
integration's own README and `RELEASING.md` were corrected at the time and this
line was missed.

Released rather than left to sit on the branch because HACS installs the latest
release, so until now the copy users actually got was the one still telling them
the install path was awkward.

### 2.5.2

**Fixes every bridge button showing up as "Cync LAN Bridge"** - the same fault
2.5.1 fixed for group power switches, which turned out to be more widespread
than that release assumed.

All six home-wide button types were affected: Query mesh credentials, Sync hub
clock, and the Delete buttons for scenes, schedules, automation bindings and
groups. Since there is one Delete button per scene, schedule and group, a
populated account produced a long list of buttons with the same name and no
way to tell which was which.

They all inherit from one base class that was missing `has_entity_name`, so
this is a one-line fix covering all six rather than six separate ones.

Also adds a test that scans the source for the general mistake - an entity
declaring a translated name without the flag that makes it apply - rather than
checking the eight classes known to have hit it. The same bug reaching a
release twice is what prompted it.

### 2.5.1

**Fixes group power switches all showing up as "Cync LAN Bridge".** Every one
of them fell back to the bridge's own device name instead of "<group> power",
so a home with several groups got several identically-named switches with
nothing to tell them apart.

The cause: these switches declared a translated name but not
`has_entity_name`, and Home Assistant only applies translated entity names
when that is set. Every other bridge-attached entity in the integration either
sets it or sets a plain name; this one did neither.

Only affects the experimental group power switches - reported from a real
install.

### 2.5.0

**Writes to a sleeping motion sensor are now refused instead of vanishing.**

Battery devices - motion sensors, and the wireless switches and remotes that
share their behaviour - only join the mesh while awake. A settings or schedule
write aimed at a sleeping one never reaches it. Previously the two
`experimental_set_motion_sensor_*` actions and the "Experimental commands"
schedule form sent regardless, so the command disappeared and nothing said why.

All of them now check the device is awake first and refuse with an error asking
you to hold its off button for five seconds until the LED turns green. The
guided "Edit motion sensor settings" wizard already did this; the other three
paths did not, and now share the same check.

The check is the device's ordinary online status, which is what the real Cync
app uses too - its wake-up screen watches the same availability signal every
device type reports, and no separate "discoverable" state exists to detect.
Where this deliberately differs from the app: the app sends anyway and reports
success without transmitting. A silent no-op there is indistinguishable from a
wrong opcode, and would send you debugging the protocol instead of pressing a
button.

### 2.4.2

**Corrects the installation instructions.** Earlier versions told you HACS
installs would not work, because the integration lived on a branch that was not
the repository's default and HACS only ever tracks the default. That has since
changed - `feature/ha-custom-component` *is* the default branch now - so HACS
picks the integration up normally as a custom repository, and releases are
offered as updates to existing installs.

The old note stayed behind after the branch change and was actively steering
people to the manual-install path for no reason. Removed from the integration
README and the repository README; `RELEASING.md` now records why the default
branch matters, so it does not get moved back by accident.

Documentation only - no functional change.

### 2.4.1

**A proper icon.** The integration now uses the Cync mark from the Home
Assistant brands repository - the same one Home Assistant already shows for
Cync devices - with a blue "LAN" badge in the corner marking it as the
local-control integration rather than the official cloud one. The previous
icon was a generic bulb.

Dark-theme variants are included for the first time, so the mark stays
visible on a dark background instead of being a black glyph on black.

Where it shows up: Home Assistant serves brand images out of an integration's
own `brand/` folder from **2026.3.0** onwards, so on that version or newer the
icon appears on the integrations page and in HACS once the integration is
installed. It will *not* appear in the HACS browse-and-download list before
installation - nothing is on disk to serve at that point, and that listing
falls back to the central brands CDN, which has no entry for this domain.
Getting it there needs a pull request against `home-assistant/brands`.

Icons are regenerated by `scripts/make_brand_icons.py`. Provenance and the
trademark notice that travels with the source art are recorded in
`LICENSE-3RD-PARTY`.

Cosmetic only - no functional change.

### 2.4.0

**New: "Use the alternate 'bare' hub envelope" (experiment)**, in
Configure → General settings, shown once experimental commands are enabled.

Hub commands - scenes, schedules, automations, groups, and the hub queries -
are sent with a 7-byte block that addresses a mesh device. A pass over the
decompiled Cync app found that all fifteen of its own hub command classes
skip that block entirely, which makes sense: a hub command is not addressed
to a mesh device, so there is nothing to route to.

That is a reason to suspect our shape, not proof it is wrong - the app talks
phone-to-device, while this integration sits between device and cloud. So
rather than change what everyone sends, this adds the alternative as a
toggle. If hub commands do nothing for you with it off, turn it on and try
again, then report which setting worked.

Unlike the other advanced options here, it applies **immediately** - no
reload, no restart. Flipping between the two is meant to be cheap, because
an experiment nobody can be bothered to finish answers nothing.

Requires `cync-lan` 0.5.0, which the manifest now pins. If an older library
is somehow installed, the toggle cannot take effect; the integration logs a
warning saying so rather than letting a silent no-op look like a result.

Everything else is unchanged, and the default is exactly what previous
versions sent.

### 2.3.0

**New: "Capture unrecognised packets to a log file" (advanced)**, in
Configure → General settings.

The underlying library has always been able to dump the raw hex of anything
that connects to the local port but does not speak Cync - it was just gated
behind an environment variable, which is not something you can set on Home
Assistant OS. So the one situation it exists for, "something is connecting
and I have no idea what", was exactly the situation you could not use it in.
Diagnosing it otherwise needs `tcpdump` on the host, which HA does not ship.

Turn it on, restart, and unrecognised traffic is written as hex to its own
log file under the integration's config directory. Usually the ASCII in that
hex names the client outright.

It needs a **full restart**, not a reload: the library reads this setting at
import time, so an already-imported module keeps the old value. The option's
description says so.

Off by default - it is noisy, and only useful when you are actually chasing
something.

### 2.2.1

**Fixed: "Ready to control" read false for almost every device.** It was a
per-device entity reporting whether that device held its own live connection -
but that is not what determines whether a device can be controlled. Commands
are sent to a random sample of the whole connection pool with the target named
inside the packet, so *any* ready connection can drive *any* device.

In a real log, 43 devices had identified themselves while only 10 still held
their own connection: the other 33 showed "not ready to control" while being
perfectly controllable through someone else's. The entity has moved to the
**Cync LAN Bridge**, where it answers the question that actually matters - is
anything currently able to carry a command - with `sessions` and
`ready_sessions` counts as attributes, because "0 of 10 ready" and "nothing
connected at all" are very different problems.

Whether an individual device holds its own connection is a real question and
already answered by its **IP address** / **Connected via** sensors.

### 2.2.0

Requires `cync-lan` 0.4.0.

**New: an Identify button on every device.** Press it and the device
announces itself, so you can work out which physical bulb or switch an entity
actually is. It uses Home Assistant's own identify device class, so it shows
up as the standard affordance rather than another experimental button - and
it is the most likely of the experimental commands to work, because it rides
the same path as the indicator-LED command, the one confirmed against real
hardware.

Unlike the rest of the experimental set, Identify is **not** gated - it is
non-destructive, self-limiting, and useless if you have to go turn something
on before you can find a light.

**New (experimental, dimmers only): Dimmer LED bar and Dimmer LED
brightness.** These control the row of level LEDs on a dimmer switch, which
is a different thing from the small status LED the existing indicator-LED
entities drive. The mode has exactly two options, "briefly display" and
"always on" - the protocol has no "off", so the bar cannot be disabled.

Not created for binary switches, which have no level bar.

**New (experimental): Sync hub clock**, on the bridge. 2.1.0 added a Hub
clock sensor that shows drift; this corrects it, pushing Home Assistant's
current time and UTC offset to the hub. Native Cync Schedules fire off the
hub's clock rather than Home Assistant's, so drift there shifts when they run
and nothing on the HA side compensates.

### 2.1.0

Requires `cync-lan` 0.3.0.

**New: five more protocol commands, all reachable from the UI** (with
experimental commands enabled). On the **Cync LAN Bridge** device:

| Entity | What it does |
|---|---|
| **Hub firmware version** | Asks the hub what firmware it is running, plus its MAC and setup code as attributes. Distinct from the version on each device page, which comes from the cloud export - a mismatch means the export is stale. |
| **Hub clock** | The date and time the hub thinks it is. Native Cync Schedules fire off *this* clock, not Home Assistant's, so a hub whose time has drifted runs its automations at the wrong moment. Nothing else showed that. |
| **Delete automation binding for *&lt;schedule&gt;*** | Removes what makes a Schedule fire, without deleting the Schedule itself. There was previously no way to do this at all. |
| **Delete group *&lt;name&gt;*** | Deletes a Cync device group from the mesh. |

The two query sensors are **disabled by default** - each poll puts a real
command on the mesh, and neither value changes quickly. The two delete
buttons are disabled by default for the same reason the existing ones are:
Home Assistant has no "are you sure?" for a button press.

A query that times out keeps its previous value rather than blanking. These
commands' reply channel is unconfirmed, so an occasional miss is expected and
should not look like the hub disappeared.

**Deliberately not included.** Three commands in the protocol flash device
firmware (`StartHubFirmwareUpdates` and two Wi-Fi OTA siblings). Every other
experimental command here fails safe - a wrong predicted envelope byte means
the device ignores the packet - but these do not, so no code path capable of
sending them exists. A fourth, "query available firmware updates", is also
left out: its reply is a variable-length structure that would produce
plausible-looking nonsense if decoded wrongly, and there is no capture to
check a decoder against. Both are documented in `docs/mesh_opcodes.md`.

### 2.0.0

**Breaking: the `experimental_*` actions are now opt-in.** If you call any of
them from an automation or script, enable them first or those calls will fail:

> Settings -> Devices & services -> Cync LAN -> **Configure** -> General
> settings -> **"Enable experimental commands (advanced)"**

They are off by default because every one sends a mesh command whose wire
format is *predicted* from a length formula rather than confirmed against a
real packet capture, and most have never been exercised on real hardware.
Previously they sat in Developer Tools and the automation picker looking as
ordinary as any other action, with only the name prefix as a warning.

**Requires `cync-lan` 0.2.1.** Earlier builds of that library pinned `pyyaml`
to exactly `6.0.2` while Home Assistant requires `PyYAML==6.0.3`, so this
integration's requirements could not be installed at all. 0.2.1 relaxes the
pin. It also brings a fix worth knowing about: six hub
commands (create/delete scene, create/delete schedule, add/toggle automation)
were sending a length field one byte short, so device firmware read a
truncated body and the command silently did nothing. If you tried these and
nothing happened, that is why. They are still experimental - the framing is
correct now, which is not the same as confirmed working.

**Fixed: reauthentication could never complete.** When the cached cloud token
expired, the reauth flow crashed on its final step - Home Assistant rejects
creating a new entry from a reauth flow, which is what this did. It now
updates the existing entry in place. Anyone who hit an expired token and could
not get back in should be able to now.

**Fixed: a newly-added device was ignored for the first 15 minutes after a
reboot.** The "re-export as soon as a new device appears" cooldown compared
against a marker initialised to zero, and `time.monotonic()` counts from boot
on Linux - so for the first 15 minutes of uptime the check suppressed the very
first trigger. A rebooted Raspberry Pi or a restarted HA OS would quietly skip
a device it had just discovered.

**Fixed: several operations blocked Home Assistant's event loop**, which shows
up as the whole instance stuttering: creating the config directory, checking
the export file, and the periodic refresh all did file I/O directly on the
loop.

**Fixed:** command acknowledgements for brightness, colour temperature and RGB
ignored the sub-device id, so on a multi-outlet device they could update the
wrong entity's state.

**New: every experimental command is now reachable from the UI**, instead of
only from Developer Tools where each one wanted a numeric scene or group ID
that the Cync app never shows you. With the option enabled:

- **Buttons** on the Cync LAN Bridge device: *Query mesh credentials*, and a
  *Delete scene* / *Delete schedule* button per scene and schedule, each
  carrying its own ID. The delete buttons arrive **disabled** - Home Assistant
  has no "are you sure?" dialog for a button, and recreating a deleted scene
  means going back to the phone app, so enabling the entity is the
  confirmation step.
- **Entities** for the commands that have state: group power (per group), and
  MultiColor gradient mode and segment count (per colour-capable device).
- **Guided forms** under Configure -> Experimental commands for the rest -
  push an automation to the hub, add or remove a device in a scene or group,
  write a motion-sensor schedule slot, and program MultiColor segments. These
  take several parameters and have nothing to read back, so a form with real
  device/scene/group pickers fits better than an entity.

**New action: `experimental_query_mesh_credentials`** reads your BTLE mesh name
and password from a connected hub. These are what `cync-lan-ble-provision`
needs to add a new device to your *existing* mesh rather than only a
factory-default one. The password is your mesh's shared secret, so it is
returned as action response data (or shown in a dismissible notification from
the button) rather than written to the log. Experimental: the response channel
is unconfirmed and may simply time out on your hardware.

Internal: the test suite could not be collected at all - a misplaced pytest
declaration meant all 461 tests had been silently not running, and no workflow
ran them either. Both fixed; tests and type checks now run on every push and
pull request.

### 1.4.0

- Add Bluetooth discovery for factory-default (never-provisioned) Cync
  devices (`manifest.json`'s new `"bluetooth"` matcher: local name
  `telink_mesh1`, Telink's manufacturer ID). Unlike DHCP discovery, this
  does **not** lead into the account-setup flow - a factory-fresh device
  isn't part of any Cync account yet, so doing that would be premature.
  Instead it shows an informational step pointing at the official Cync
  app or the `cync-lan-ble-provision` CLI tool. Doesn't declare
  `"bluetooth"` as a manifest dependency, since this integration never
  calls the runtime bluetooth API - only receives the discovery match.
  See `quality_scale.yaml`'s `discovery` entry.

### 1.3.0

- Add DHCP discovery: Home Assistant now proactively offers to set up this
  integration when a device with a Cync-pattern DHCP hostname (`GE_*`)
  appears on the network, instead of requiring you to find it yourself in
  **Add Integration**. Still leads to the same account-credentials form -
  this integration is set up per Cync account, not per device, so
  discovery is a nudge into setup, not a replacement for entering your
  Cync account email/password. See `quality_scale.yaml`'s `discovery`
  entry.

### 1.2.0

- Pass `mypy --strict` cleanly (see `mypy.ini`) - quality_scale.yaml's
  `strict-typing` (platinum) is now done. No functional change to this
  integration's own code, aside from bumping the `cync-lan` dependency to
  `>=0.1.2` - that release fixes a real bug this pass caught upstream:
  `MqttSink.pub_online` was declared as a plain `def` instead of
  `async def` in `cync-lan`'s `protocols.py`, even though every real
  implementation (including this integration's own `CyncLanBridge`) is
  async. Harmless at runtime (Python doesn't enforce `Protocol`
  conformance dynamically) but was a real static-typing defect.

### 1.1.0

- Depend on the published `cync-lan` PyPI package instead of a vendored
  copy (`custom_components/cync_lan/vendor/`) - see `quality_scale.yaml`'s
  `dependency-transparency`/`async-dependency` entries. No functional
  change; this is the packaging fix those entries describe.

### 1.0.0

- First major version number. This is a versioning milestone, not a
  stability claim: nothing functional changed from 0.5.0, every
  `experimental_*` service is exactly as experimental as before, and
  entities disabled-by-default remain disabled-by-default. See
  `quality_scale.yaml` and this integration's own README for what's
  actually confirmed-working versus still unconfirmed.

### 0.5.0

- Add a per-device MITM-mode toggle switch (diagnostic category, disabled
  by default - most users never need it): puts a device into the
  underlying package's traffic-capture debug mode, useful for helping add
  support for new devices/features. Reads/writes the device's own TCP
  session directly rather than the underlying package's MQTT-discovery
  mechanism, which this integration's static entity model has no use for.
- Add two connection-diagnostic sensors, exactly one per device depending
  on how it connects: **IP address** for WiFi-capable devices (the LAN
  address of its own direct connection), or **Connected via** for
  BTLE-mesh-only devices (which WiFi-capable device is currently relaying
  its status - a new `CyncDevice.relay_source` tracking addition in the
  underlying package, set at every mesh status/MeshInfo parse site).

### 0.4.1

- Fix devices going unavailable when they lose power or network not being
  reflected in Home Assistant, showing stale last-known state indefinitely
  instead. The existing detection path (a mesh status broadcast reporting a
  device with a "not recently seen" flag) only catches this if *other*
  devices keep relaying broadcasts that still mention the affected one -
  it never covered the common case of a WiFi-connected plug/switch/bulb
  that owns its own direct TCP connection simply losing power, since that
  device's own dev_id just stops appearing in broadcasts entirely rather
  than appearing with a stale flag. The underlying package's code even
  already detected this exact scenario ("device probably dropped the
  connection (lost power)") but never acted on it - the TCP session
  closing now immediately marks that device offline.

### 0.4.0

- Add `CyncDevice.set_multicolor_gradient_mode()`/`set_multicolor_segment_count()`/
  `set_multicolor_segments()` in the underlying package - 3 confirmed wire
  primitives for programming a custom MultiColor scheme (per-segment RGB
  color on segmented/dynamic light strips), plus standalone
  `experimental_set_multicolor_gradient_mode`/
  `experimental_set_multicolor_segment_count`/
  `experimental_set_multicolor_segments` services. Per-segment position and
  color are independently optional, confirmed directly from the app's own
  data model. This integration does not orchestrate the full multi-send
  sequence a real custom scheme needs (chunking/ordering for more than 2
  segments) - only the 3 confirmed primitives are exposed; see the
  README's "Known limitations" for what that means in practice. Two
  related commands (`SetMultiColorSchemeDirectCommand`'s "entertainment"/
  live-streaming variant, `SetMultiColorBitmapCommand`'s tile/matrix
  bitmap data) remain out of scope - both dispatch via methods never
  traced anywhere else in this project's research, not just untested.
- Add a dedicated, always-on `experimental_features.log` (no flag to
  enable) that records every single `experimental_*` command/service
  invocation the moment it runs, independent of the console warnings that
  only fire once per process per command name - attach this file (not the
  full HA log) when reporting a bug about any experimental feature. Never
  blocks the actual command if the log file itself can't be created (e.g.
  a permissions issue) - falls back to a one-time error in the main log
  instead.

### 0.3.0

- Add `CyncDevice.remove_from_scene()` in the underlying package - the
  counterpart to `add_to_scene()`, removing one device's captured state
  from an existing scene without deleting the scene or affecting its other
  members. Confirmed via decompiled app source
  (`RemoveDeviceSceneCommand`); unlike `add_to_scene()`, both device
  product-family code paths are dispatch-confirmed here, not just the
  common one.
- Add `experimental_add_device_to_scene` and
  `experimental_remove_device_from_scene`: standalone versions of the same
  functions `experimental_push_automation_to_hardware` already uses
  internally, so they can be tested against a scene you already have
  (Cync-app-created or otherwise) without going through the full
  automation-push flow.

### 0.2.0

- Add native Scene/Schedule creation: `create_scene`, `create_schedule`, and
  `add_automation` in the underlying package, plus
  `CyncDevice.add_to_scene()`, using payloads traced from the real Cync
  Android app's decompiled source. These are the first commands that read a
  response back off the wire - the hub allocates a scene/schedule ID and
  reports it back asynchronously over a distinct notification channel,
  decoded by a new HDLC/PPP frame parser. Whether that notification channel
  rides the same connection this integration already intercepts is
  unconfirmed, so both functions return `None` on a 10-second timeout
  instead of raising.
- Add `experimental_push_automation_to_hardware`: reads an existing HA
  automation's own trigger/condition/action config and pushes it onto the
  Cync hub as a native Scene + Schedule, so the same schedule keeps firing
  even if Home Assistant or the network goes down. Strictly scoped to a
  plain time trigger, an optional day-of-week condition, and
  `light.turn_on` actions targeting Cync lights only with a single color -
  brightness, effects, light groups, and anything else a Cync Scene can't
  represent are rejected outright rather than silently dropped. See the
  README's "Pushing an HA automation to Cync hardware" section.
- Add `CHANGELOG.md` (this file) and a `RELEASING.md` release process for
  the maintainer - this integration had no version history or HACS release
  process until now, despite `manifest.json` sitting at `0.1.0` since its
  first commit.

### 0.1.0

Initial buildout - never actually tagged/released via HACS despite the
version number; consolidated here retroactively from the full commit
history now that a real release process exists.

**Core integration**

- Config flow: connect a Cync account (email/password + emailed one-time
  code), confirm the device count, and start the local TCP/TLS listener -
  no YAML configuration.
- Reauthentication flow triggered automatically when the cached cloud
  session can't be silently refreshed.
- Options flow for local port and the automatic device-list export refresh
  interval.
- Auto-generates a self-signed TLS certificate and points the underlying
  package's config/state directory at Home Assistant's own config
  directory, instead of requiring manual certificate/path setup.
- Vendors the `cync-lan` package's protocol code directly into the
  integration (`custom_components/cync_lan/vendor/`) to work around Home
  Assistant's stricter dependency-resolution requirements, kept in sync
  with the upstream package by hand.
- Diagnostic entities and repair/reconfigure flows for common failure
  modes: TCP server crashes, stale sessions, connection churn, and
  cloud-export token-cache instability.

**Entities**

- `light`: on/off, brightness, color temperature, RGB, and effects, per
  device capability.
- `light` (group, opt-in): one aggregate entity per Cync device group,
  built on Home Assistant's own group-light helper, with an option to hide
  individual group members from dashboards.
- `switch`: on/off switches and outlets/plugs, plus one entity per saved
  Cync Schedule to enable/disable it.
- `fan`: fan controllers with percentage and preset-speed control.
- `binary_sensor`: standalone motion/occupancy sensor accessories, a
  secondary motion entity on light/switch models with a built-in
  occupancy sensor, and a Wi-Fi-presence occupancy sensor from the app's
  own TCP login packets.
- `select`/`number`/`switch` (config): indicator-LED mode, color,
  brightness, and blink-on-WiFi-disconnect - confirmed working on real
  hardware.
- `sensor` (diagnostic): one entity per native Cync motion-sensor schedule
  slot, read-only.
- `scene`: one activatable entity per saved Cync Scene.

**Groups, Scenes, and Schedules (experimental services)**

- `experimental_set_indicator_led`, `experimental_set_motion_sensor_settings`
  (plus a guided "Edit Motion Sensor Settings" options-flow wizard that
  walks through waking the physical sensor first),
  `experimental_set_motion_sensor_schedule`.
- `experimental_execute_scene`, `experimental_delete_scene`,
  `experimental_delete_schedule`, `experimental_toggle_automation`.
- `experimental_set_group_power`, `experimental_set_group_membership`.
- All prefixed `experimental_` as a visible risk signal - most of these
  commands' outer envelope byte is a predicted value pending real-hardware
  confirmation, tracked per-command in the parent project's
  `docs/mesh_opcodes.md`.

**Reliability fixes found along the way**

- Command-ack callbacks not seeding initial entity state.
- State updates never reaching the HA frontend because a callback wasn't
  marked `@callback`.
- Dimmable switches losing brightness control after being reclassified from
  `light` to `switch`.
- Rejected devices being silently resurrected due to a missing `await`.
- Blocking file I/O on the event loop during config/group parsing and cloud
  export writes.
- A singleton-state bug across repeated integration reloads.
