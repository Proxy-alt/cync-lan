# This branch has moved

The `cync-lan` core protocol library now lives in its own repository:

**https://github.com/Proxy-alt/cync-lan-lib**

Its `main` branch is a direct continuation of this branch, with the full
history and all `cync-lan-v*` tags.

## Why

This branch shared `Proxy-alt/cync-lan` with the MQTT add-on (`python`) and
the Home Assistant integration (`feature/ha-custom-component`), so all three
published into one GitHub release list.

That broke HACS. HACS does not read the "Latest" flag - it calls the
`/releases` list endpoint and takes the first non-draft, non-prerelease
entry in list order, newest-first by date. So a `cync-lan-v*` release cut
after an integration release became what HACS advertised as the
integration's update, pointing at this tree, which has no
`custom_components/` directory. The download failed and the update was
uninstallable.

See `RELEASING.md` in `cync-lan-lib` for the full explanation.

## This branch is frozen

Its release workflow has been removed so that a push here cannot cut a
GitHub release and re-break HACS. **Do not commit here - open your change
against `Proxy-alt/cync-lan-lib` instead.** This branch is kept only so old
commit and tag links keep resolving, and may be deleted once they no longer
matter.
