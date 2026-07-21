# Releasing

This repository ships two separately-versioned artifacts. Releasing one
does not imply releasing the other.

| Artifact | Version lives in | Changelog | Distribution |
|---|---|---|---|
| Home Assistant `cync_lan` custom_component | `custom_components/cync_lan/manifest.json`'s `version` | `custom_components/cync_lan/CHANGELOG.md` | HACS (custom repository) / GitHub Release, on the `feature/ha-custom-component` branch |
| `cync-lan` Python package (Docker/MQTT add-on) | `pyproject.toml`'s `version` | root `CHANGELOG.md` | `pip install git+...@python`, consumed by `hass-addons`' Docker build |

## Releasing the Home Assistant custom_component (HACS)

1. Bump `custom_components/cync_lan/manifest.json`'s `version` field
   (semver - bump the minor version for a normal feature release, patch for
   a fix-only release).
2. Add a new entry at the top of `custom_components/cync_lan/CHANGELOG.md`
   for that version, above the previous entry. Write it for users, not as a
   commit-log dump: group related changes, explain what changed in
   practice, and call out anything experimental or unconfirmed the same way
   existing entries do.
3. Update `custom_components/cync_lan/README.md` if the release adds new
   entities, services, or configuration options - it should always describe
   the integration as it exists *right now*, not as of some earlier version.
4. Commit these changes on `feature/ha-custom-component`.
5. Run the full test suite (`pytest tests/components/cync_lan/`) and confirm
   it passes before tagging anything.
6. Tag the release and push the tag:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

7. Create the GitHub Release from that tag with the `gh` CLI, using the new
   CHANGELOG.md entry as the release notes body:

   ```bash
   gh release create v0.2.0 \
     --title "cync_lan v0.2.0" \
     --notes-file <(sed -n '/^### 0\.2\.0$/,/^### /p' custom_components/cync_lan/CHANGELOG.md | sed '$d')
   ```

   (That `sed` pulls just the new version's section out of the changelog so
   the release notes don't repeat the whole file - adjust the version
   number in both `sed` patterns to match what you just tagged.)

### The non-default-branch caveat

This integration currently lives on `feature/ha-custom-component`, not the
repository's default branch (`python`). HACS 2.0 does not support pointing
a custom repository at a non-default branch - it always tracks whatever
branch GitHub reports as default. Until `feature/ha-custom-component` is
merged into `python` (or promoted to the new default branch), a tagged
GitHub Release here does not make HACS offer an update to existing
custom-repository installs; see `custom_components/cync_lan/README.md`'s
"Installing the integration" section for the current manual-install
workaround. Tagging releases now is still worthwhile - it gives the
integration real version history and release notes ahead of that merge,
and `gh release list` / the GitHub Releases page becomes a real changelog
in the meantime.

## Releasing the Python package (`python` branch)

This side has no formal release process today - no `pyproject.toml` bump
has ever been paired with a git tag or a GitHub Release, and
`pyproject.toml`'s version has drifted ahead of what `CHANGELOG.md`
documented before this pass fixed it (`hass-addons/cync-lan/CHANGELOG.md`,
which tracks this exact package via its Docker build's
`pip install git+...@python`, had been kept current independently and was
folded back in here - see the subtree note below).

Until a real tagging scheme is adopted for this side, keep at minimum:

1. `pyproject.toml`'s `version` bumped for any change that affects behavior.
2. A matching entry added to the top of root `CHANGELOG.md`.

If you do want to start tagging real releases for this artifact too, use a
distinct prefix (e.g. `cync-lan-v0.0.6b45`) rather than bare `vX.Y.Z`, since
that tag namespace is used by the HA custom_component's releases above and
the two version numbers do not correspond to each other.

## Keeping `hass-addons` in sync

`hass-addons/cync-lan/` (the Home Assistant *App*/add-on packaging,
distinct from both artifacts above) vendors this repository's `python`
branch in via `git subtree` under `cync-lan/upstream/`, refreshed
automatically by a scheduled GitHub Actions workflow in that repository. Its
`CHANGELOG.md` and `README.md` device-support claims should track whatever
this repository's own root `CHANGELOG.md`/`docs/known_devices.md` say -  if
you find them out of sync, check whether that workflow has been failing
before manually reconciling the two.
