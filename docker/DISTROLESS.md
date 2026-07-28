# Distroless base: measured comparison

Prototype only. `docker/Dockerfile` remains the image the release workflow
builds; `docker/Dockerfile.distroless` exists to make this decision on evidence
rather than on the reputation of the word "distroless".

All figures below were measured locally on **linux/arm64** (Apple Silicon),
Trivy 0.72.0, `--scanners vuln,secret`, 12 runs per timing. The same platform as
the Docker Scout report that started this.

Three images, so the base and the interpreter version can be told apart:

| tag | base | python |
|---|---|---|
| `slim` | `python:3.14-slim-trixie` | 3.14.6 (production today) |
| `slim313` | `python:3.13-slim-trixie` | 3.13 (control) |
| `distroless2` | `gcr.io/distroless/python3-debian13:nonroot` | 3.13.5 |

`distroless2` vs `slim313` isolates the **base**. `slim` vs `slim313` isolates
the **interpreter**.

## Size

| image | size | delta |
|---|---|---|
| `slim` | 355 MB | — |
| `distroless2` | **191 MB** | **-46%** |

## Vulnerabilities

| severity | slim (3.14) | distroless2 | delta |
|---|---|---|---|
| CRITICAL | 4 | **0** | **-4** |
| HIGH | 19 | 21 | +2 |
| MEDIUM | 54 | 55 | +1 |
| LOW | 66 | 46 | -20 |
| UNKNOWN | 28 | 3 | -25 |
| **TOTAL** | **171** | **125** | **-46** |
| distinct vulnerable packages | 37 | **17** | -20 |
| secrets | 0 | 0 | — |

All four CRITICALs on slim are `perl-base`. Distroless has no perl, so they are
genuinely gone rather than reclassified.

### The +2 HIGH is a measurement artifact, not a regression

This is the part worth reading twice. The extra HIGHs on distroless are
`libpython3.13-minimal`, `libpython3.13-stdlib`, `python3.13-minimal`,
`python3.13-venv` (the same 3 CVEs counted once per package name) and
`libexpat1` (6).

They appear **because distroless uses Debian's packaged python3**, which Trivy
can attribute to a source package and match against a CVE feed.

`python:3.x-slim` compiles CPython from source into `/usr/local`. Trivy reports
**no python package at all** for it, and no `libexpat1` either — `dpkg-query`
confirms neither is registered. Its interpreter and its bundled expat are not
clean, they are *invisible to the scanner*.

So the honest reading is that distroless's numbers are **more complete**, not
worse. Comparing the two totals as though they measured the same surface would
be wrong in slim's favour.

## Performance

Container start to interpreter ready:

| image | median | min | p90 |
|---|---|---|---|
| `slim` | 181.0 ms | 168.6 | 194.0 |
| `slim313` | 179.3 ms | 166.3 | 196.4 |
| `distroless2` | 187.1 ms | 180.1 | 197.8 |

Importing the full application stack:

| image | median | min | p90 |
|---|---|---|---|
| `slim` | 433.6 ms | 422.8 | 447.6 |
| `slim313` | 429.3 ms | 402.8 | 460.2 |
| `distroless2` | **393.7 ms** | 381.8 | 396.9 |

**There is no meaningful runtime performance gain, and none should be
expected.** It is the same CPython, the same glibc and the same manylinux
wheels; nothing in the base affects steady-state throughput. The ~40 ms faster
import and ~6 ms slower container start are startup costs on a daemon that runs
for weeks. Treat them as noise-level, not as a reason to switch.

`slim` (3.14) and `slim313` (3.13) are within 4 ms of each other, so the
interpreter version does not matter here either. An earlier run suggested 3.14
was ~25% slower to import; that was cold page cache on the first measurement
and did not reproduce.

### One real trap, worth recording

The first distroless build measured **687.5 ms** to import — 60% *worse* than
slim. Cause: the Dockerfile deleted `__pycache__` to save space, and with
`PYTHONDONTWRITEBYTECODE=1` set at runtime nothing ever regenerated it, so the
entire dependency tree was re-parsed from source on every single start.

Replacing that with `python -m compileall` in the builder cost 14 MB and
returned 294 ms. Any minimal-image work that strips bytecode to save space is
making this mistake.

## What was verified

- Full application import (`cync_lan`, `cync_lan_mqtt.main`, cryptography,
  uvloop, aiomqtt, fastapi, uvicorn, aiohttp, yaml, pydantic)
- Self-signed certificate generation via
  `nCyncServer._ensure_self_signed_cert` — 1696 B cert, 3243 B key. This is
  what makes distroless viable at all, since there is no shell to run `openssl`
  from.
- Timezone handling. Distroless ships `/usr/share/zoneinfo` but no
  `/etc/localtime`, so `tzlocal` emits a "cannot find any timezone
  configuration" warning and falls back to UTC — exactly what slim does anyway.
  An explicit `TZ=America/New_York` resolves correctly in both. Cosmetic
  difference only, which matters given native Cync schedules are
  timezone-sensitive.

## What was NOT verified

- **No live device session.** No Cync hardware or MQTT broker was available, so
  TCP/TLS serving to real devices, the MQTT path and the export UI are all
  untested on this base. Imports succeeding is not the same as working.
- **arm64 only.** amd64/armv7 were not built.

## Costs that no benchmark shows

- **No shell.** `docker exec ... bash` is impossible. For a project whose
  troubleshooting is largely "get into the container and check DNS, certs and
  connectivity", this is the significant loss. The `:debug` variant restores a
  busybox shell and with it some of the surface just removed.
- **The interpreter version stops being ours to choose.** `python:3.14-slim`
  moves when this project decides to. Here it is whatever Debian ships, and a
  base bump can retire the interpreter underneath the wheels.
- **It does not help the Home Assistant add-on**, which must build on
  `ghcr.io/home-assistant/*-base-python` for bashio and s6-overlay. Roughly half
  the distribution surface is unaffected by any of this.

## Recommendation

Worth adopting for size and for eliminating the perl CRITICALs, **not** for
performance — there is no performance case.

Before it could replace `docker/Dockerfile` it needs a real device session, an
amd64/armv7 build, and a decision about how users are expected to debug without
a shell. Until then this file and `Dockerfile.distroless` stand as the record of
what was measured.
