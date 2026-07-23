# Releasing

This repository ships two separately-versioned artifacts. Releasing one
does not imply releasing the other.

| Artifact | Version lives in | Changelog | Distribution |
|---|---|---|---|
| Home Assistant `cync_lan` custom_component | `custom_components/cync_lan/manifest.json`'s `version` | `custom_components/cync_lan/CHANGELOG.md` | HACS (custom repository) / GitHub Release, on the `feature/ha-custom-component` branch |
| `cync-lan` Python package (Docker/MQTT add-on) | `pyproject.toml`'s `version` | root `CHANGELOG.md` | `pip install git+...@python`, consumed by `hass-addons`' Docker build |

## Releasing the Home Assistant custom_component (HACS)

Tagging and the GitHub Release itself are now automated - see
"Automated prereleases" below. All you do is:

1. Bump `custom_components/cync_lan/manifest.json`'s `version` field
   (semver - bump the minor version for a normal feature release, patch for
   a fix-only release).
2. Add a new entry at the top of `custom_components/cync_lan/CHANGELOG.md`
   for that version, above the previous entry, **using that exact version
   string as the `### ` heading** - the release workflow parses this
   heading out verbatim to find the entry and to know what to tag. Write
   it for users, not as a commit-log dump: group related changes, explain
   what changed in practice, and call out anything experimental or
   unconfirmed the same way existing entries do.
3. Update `custom_components/cync_lan/README.md` if the release adds new
   entities, services, or configuration options - it should always describe
   the integration as it exists *right now*, not as of some earlier version.
4. Commit these changes on `feature/ha-custom-component` and push.
5. Run the full test suite (`pytest tests/components/cync_lan/`) and confirm
   it passes before pushing, same as any other change - the release
   workflow does not run the test suite itself.

Pushing to `feature/ha-custom-component` with `manifest.json` in the diff
is what triggers the release - see "Automated prereleases" below for what
happens next and how to do it by hand if you ever need to.

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

This side had no formal release process for a long time - no
`pyproject.toml` bump was ever paired with a git tag or a GitHub Release,
and `pyproject.toml`'s version drifted ahead of what `CHANGELOG.md`
documented before an earlier pass fixed it
(`hass-addons/cync-lan/CHANGELOG.md`, which tracks this exact package via
its Docker build's `pip install git+...@python`, had been kept current
independently and was folded back in - see the subtree note below).
Tagging and the GitHub Release are now automated too, same as the HA
integration above:

1. `pyproject.toml`'s `version` bumped for any change that affects behavior.
2. A matching entry added to the top of root `CHANGELOG.md`, **using that
   exact version string as the `### ` heading** - same parsing requirement
   as the HA integration's changelog above.
3. Commit and push - to either `feature/ha-custom-component` or `python`,
   whichever the change actually landed on first (this file is shared;
   see the porting workflow elsewhere in this project's history). The
   release workflow is idempotent (see below), so it's safe if the same
   version later gets pushed to the other branch too via porting.

Tags use a distinct prefix, `cync-lan-vX.Y.Z` (e.g. `cync-lan-v0.0.6b48`),
not bare `vX.Y.Z` - that tag namespace belongs to the HA custom_component's
releases above, and the two version numbers do not correspond to each
other.

## Automated prereleases

`.github/workflows/prerelease_ha_integration.yml` (on
`feature/ha-custom-component` only) and
`.github/workflows/prerelease_python_package.yml` (on both branches, since
`pyproject.toml` is shared) watch for pushes that change
`custom_components/cync_lan/manifest.json`/`pyproject.toml` respectively.
On a matching push, each workflow:

1. Reads the new version out of the version file.
2. Checks whether a tag for that version already exists (`git ls-remote
   --tags`) - if so, does nothing. This is what makes it safe for the
   python package's workflow to run on both branches: whichever branch the
   version lands on second just no-ops.
3. Extracts that version's own section out of the matching `CHANGELOG.md`
   (the `### <version>` heading must match the version file *exactly*, or
   this step fails loudly rather than silently publishing an empty/wrong
   release).
4. Tags the current commit and creates a GitHub Release from it via `gh
   release create --prerelease` - always a prerelease, for both artifacts,
   since nothing in this project has reached a "stable" designation yet.

Both are also `workflow_dispatch`-triggerable from the Actions tab, for a
manual re-run (e.g. if a push happened before the version file finished
propagating, or to retry after a transient failure) without needing an
empty commit.

**Interaction with `container-package-publish.yml`**: that workflow
already triggers on `release: published` to build/push the Docker image,
and a prerelease still counts as "published" - it fires for every prerelease
these two workflows create, not just python-package ones. Its `build` job
is guarded (`if: ... startsWith(github.event.release.tag_name,
'cync-lan-v')`) so it only actually runs for the python package's own
`cync-lan-v*` releases, not the HA integration's unrelated `v*` ones. Keep
this guard in mind if either tag prefix ever changes.

## Keeping `hass-addons` in sync

`hass-addons/cync-lan/` (the Home Assistant *App*/add-on packaging,
distinct from both artifacts above) vendors this repository's `python`
branch in via `git subtree` under `cync-lan/upstream/`, refreshed
automatically by a scheduled GitHub Actions workflow in that repository. Its
`CHANGELOG.md` and `README.md` device-support claims should track whatever
this repository's own root `CHANGELOG.md`/`docs/known_devices.md` say -  if
you find them out of sync, check whether that workflow has been failing
before manually reconciling the two.
