# Releasing

This repository ships three separately-versioned artifacts. Releasing one
does not imply releasing the others.

| Artifact | Branch | Version lives in | Changelog | Distribution | Tag prefix |
|---|---|---|---|---|---|
| `cync-lan` core protocol library | `core` | `pyproject.toml`'s `version` | `CHANGELOG.md` (on `core`) | PyPI, via Trusted Publishing | `cync-lan-vX.Y.Z` |
| `cync-lan-mqtt` Docker/MQTT add-on | `python` | `pyproject.toml`'s `version` | `CHANGELOG.md` (on `python`) | PyPI, via Trusted Publishing; consumed by `hass-addons`' Docker build | `cync-lan-mqtt-vX.Y.Z` |
| Home Assistant `cync_lan` custom_component | `feature/ha-custom-component` | `custom_components/cync_lan/manifest.json`'s `version` | `custom_components/cync_lan/CHANGELOG.md` | HACS (custom repository) / GitHub Release | `vX.Y.Z` |

The HA integration depends on the core library (`manifest.json`'s
`requirements`, a normal PyPI dependency - no more vendoring); the add-on
depends on it too (`pyproject.toml`). Bumping core doesn't require bumping
either consumer, and vice versa.

## Releasing the core library (`core` branch)

1. Bump `pyproject.toml`'s `version` (semver).
2. Add a matching entry at the top of `CHANGELOG.md`, **using that exact
   version string as the `### ` heading** - the release workflow parses
   this heading out verbatim.
3. Run the test suite (`pytest tests/`) and confirm it passes before
   pushing - the release workflow also runs it as a gate before tagging or
   publishing, but don't rely on CI to catch something you could've caught
   locally first.
4. Commit and push to `core`.

Tagging, the GitHub prerelease, and the actual PyPI publish are all
automated from there - see "Automated prereleases + PyPI publishing"
below.

## Releasing the add-on (`python` branch)

1. Bump `pyproject.toml`'s `version`.
2. If the change also requires a newer core version, bump the
   `cync-lan>=X.Y.Z` line in `dependencies` too.
3. Add a matching entry at the top of `CHANGELOG.md`, same heading
   requirement as above.
4. Commit and push to `python`.

## Releasing the Home Assistant custom_component (HACS)

1. Bump `custom_components/cync_lan/manifest.json`'s `version` field.
2. If the change also requires a newer core version, bump the
   `cync-lan>=X.Y.Z` line in `manifest.json`'s `requirements` too.
3. Add a new entry at the top of `custom_components/cync_lan/CHANGELOG.md`,
   above the previous entry, **using that exact version string as the
   `### ` heading**. Write it for users, not as a commit-log dump: group
   related changes, explain what changed in practice, and call out
   anything experimental or unconfirmed the same way existing entries do.
4. Update `custom_components/cync_lan/README.md` if the release adds new
   entities, services, or configuration options - it should always describe
   the integration as it exists *right now*, not as of some earlier version.
5. Run the full test suite (`pytest tests/components/cync_lan/`) against
   the installed `cync-lan` package (not a vendored copy - there isn't one
   anymore) and confirm it passes before pushing.
6. Commit and push to `feature/ha-custom-component`.

### The non-default-branch caveat

This integration currently lives on `feature/ha-custom-component`, not the
repository's default branch (`python`). HACS 2.0 does not support pointing
a custom repository at a non-default branch - it always tracks whatever
branch GitHub reports as default. Until this integration is merged into
`python` (or promoted to the new default branch), a tagged GitHub Release
here does not make HACS offer an update to existing custom-repository
installs; see `custom_components/cync_lan/README.md`'s "Installing the
integration" section for the current manual-install workaround. Tagging
releases now is still worthwhile - it gives the integration real version
history and release notes ahead of that merge, and `gh release list` / the
GitHub Releases page becomes a real changelog in the meantime.

## Automated prereleases + PyPI publishing

Each branch has its own workflow that watches for a push changing its
version file:

- `core` branch: `.github/workflows/publish_pypi_core.yml`
- `python` branch: `.github/workflows/publish_pypi_addon.yml`
- `feature/ha-custom-component` branch: `.github/workflows/prerelease_ha_integration.yml`

On a matching push, each workflow:

1. Reads the new version out of the version file.
2. Checks whether a tag for that version already exists (`git ls-remote
   --tags`) - if so, does nothing.
3. Extracts that version's own section out of the matching `CHANGELOG.md`
   (the `### <version>` heading must match the version file *exactly*, or
   this step fails loudly rather than silently publishing an empty/wrong
   release).
4. Tags the current commit and creates a GitHub Release from it via `gh
   release create --prerelease` - always a prerelease, for all three
   artifacts, since nothing in this project has reached a "stable"
   designation yet.
5. **`core` and `python` only**: runs the test suite (core branch only, no
   dedicated test suite exists for the add-on yet) and, if the tag was
   newly created, builds and publishes to PyPI via Trusted Publishing (no
   stored API token - PyPI trusts these specific GitHub Actions workflow
   runs directly). This requires a PyPI "pending publisher" configured
   once, per package, in PyPI's web UI (Account Settings -> Publishing):
   repo `Proxy-alt/cync-lan`, workflow filename `publish_pypi_core.yml` /
   `publish_pypi_addon.yml`, environment `pypi-core` / `pypi-mqtt`
   respectively.

All three are also `workflow_dispatch`-triggerable from the Actions tab,
for a manual re-run (e.g. if a push happened before the version file
finished propagating, or to retry after a transient failure) without
needing an empty commit.

**Interaction with `container-package-publish.yml`**: that workflow
(on the `python` branch) triggers on `release: published` to build/push
the Docker image, and a prerelease still counts as "published" - it fires
for every prerelease any of the three workflows above create, not just
the add-on's own. Its `build` job is guarded
(`if: ... startsWith(github.event.release.tag_name, 'cync-lan-mqtt-v')`)
so it only actually runs for the add-on's own `cync-lan-mqtt-v*` releases
- not the HA integration's `v*` ones, and not the core library's own
unrelated `cync-lan-v*` ones (a non-Docker artifact). Keep this guard in
mind if any tag prefix ever changes.

## Keeping `hass-addons` in sync

`hass-addons/cync-lan/` (the Home Assistant *App*/add-on packaging,
distinct from all three artifacts above) installs `cync-lan-mqtt` from
PyPI in its `Dockerfile`, and separately vendors this repository's
`python` branch in via `git subtree` under `cync-lan/upstream/` for
docs/changelog reference (refreshed automatically by a scheduled GitHub
Actions workflow in that repository - not the actual runtime install
path). Its `CHANGELOG.md` and `README.md` device-support claims should
track whatever this repository's own root `CHANGELOG.md`/
`docs/known_devices.md` say - if you find them out of sync, check whether
that workflow has been failing before manually reconciling the two.
