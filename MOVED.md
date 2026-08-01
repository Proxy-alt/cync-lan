# This branch has moved

The `cync-lan-mqtt` Docker/MQTT add-on now lives in its own repository:

**https://github.com/Proxy-alt/cync-lan-mqtt**

Its `main` branch is a direct continuation of this branch, with the full
history and all `cync-lan-mqtt-v*` tags.

## Why

This branch shared `Proxy-alt/cync-lan` with the core library (`core`) and
the Home Assistant integration (`feature/ha-custom-component`), so all three
published into one GitHub release list.

That broke HACS. HACS does not read the "Latest" flag - it calls the
`/releases` list endpoint and takes the first non-draft, non-prerelease
entry in list order, newest-first by date. So a `cync-lan-mqtt-v*` release
cut after an integration release became what HACS advertised as the
integration's update, pointing at this tree, which has no
`custom_components/` directory. The download failed and the update was
uninstallable.

See `RELEASING.md` in `cync-lan-mqtt` for the full explanation.

## This branch is frozen

Its release and container-publish workflows have been removed, so a push
here cannot cut a GitHub release (which can re-break HACS, as above) or push
a container image built from a stale tree. **Do not commit here - open your
change against `Proxy-alt/cync-lan-mqtt` instead.** This branch is kept only
so old commit and tag links keep resolving, and may be deleted once they no
longer matter.

The ghcr.io image continues to publish from the new repository, to the same
`ghcr.io/proxy-alt/cync-lan-mqtt` name.
