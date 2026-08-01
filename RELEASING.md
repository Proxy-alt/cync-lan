# Releasing

This repository ships one artifact: the Home Assistant `cync_lan`
custom_component, distributed via HACS.

| Artifact | Branch | Version lives in | Changelog | Distribution | Tag prefix |
|---|---|---|---|---|---|
| Home Assistant `cync_lan` custom_component | `feature/ha-custom-component` (default) | `custom_components/cync_lan/manifest.json`'s `version` | `custom_components/cync_lan/CHANGELOG.md` | HACS (custom repository) / GitHub Release | `vX.Y.Z` |

It depends on the `cync-lan` core protocol library as a normal PyPI
dependency (`manifest.json`'s `requirements`) - nothing is vendored. Two
related artifacts are released independently, from their own repositories:

- [`Proxy-alt/cync-lan-lib`](https://github.com/Proxy-alt/cync-lan-lib) -
  the `cync-lan` core protocol library.
- [`Proxy-alt/cync-lan-mqtt`](https://github.com/Proxy-alt/cync-lan-mqtt) -
  the `cync-lan-mqtt` Docker/MQTT add-on.

Bumping the core library doesn't require bumping this, and vice versa.

## Releasing

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
   the installed `cync-lan` package and confirm it passes before pushing.
6. Commit and push to `feature/ha-custom-component`.

## How HACS resolves this repository

Two separate mechanisms, and both have bitten this project.

**Which branch.** `feature/ha-custom-component` is the repository's
**default branch**, so HACS tracks it. HACS 2.0 has never supported pointing
a custom repository at a non-default branch. The default used to be
`python`, which made the integration effectively manual-install only;
making this branch the default is what fixed it. If the default is ever
moved back, HACS installs break silently and that has to be handled first.

**Which release.** HACS does **not** read the "Latest" flag. It calls the
`/releases` **list** endpoint and takes the first non-draft, non-prerelease
entry in list order - and GitHub returns that list newest-first *by date*:

```python
if release.draft:        continue
elif release.prerelease: ...
else:
    self.data.last_version = release.tag_name
    break
```

This is why the three artifacts no longer share this repository. They used
to, on the `core`, `python` and `feature/ha-custom-component` branches, and
whenever a `cync-lan-v*` or `cync-lan-mqtt-v*` release happened to be the
most recent, **HACS advertised that tag as this integration's update** -
pointing at a tree with no `custom_components/` directory, so the download
failed and the offered update was uninstallable.

The old workflows tried to prevent this by cutting the other two artifacts
with `--latest=false` and reserving **Latest** for the integration. That was
addressing a mechanism that does not exist; the flag is never consulted.
`hacs.json` has no tag filter either. The only lever HACS actually honours
is *what is in the release list at all*, so the fix was one artifact per
repository.

Historical `cync-lan-v*` and `cync-lan-mqtt-v*` **tags** are still present
here, deliberately - old links and PyPI provenance keep resolving, and tags
are invisible to HACS, which needs published releases. Do not cut a GitHub
Release against any of them.

## Automated releases

`.github/workflows/prerelease_ha_integration.yml` watches for a push to
`feature/ha-custom-component` that changes `manifest.json`.

(The file is still named `prerelease_*` so the Actions tab keeps its run
history, which GitHub keys by file path. It cuts full releases.)

### Release vs prerelease

Decided from the version string alone, by the workflow's "Classify the
version" step. There is no flag or manual toggle:

| Version   | Result                          |
|-----------|---------------------------------|
| `2.3.0`   | full release                    |
| `2.3.0b1` | prerelease (a beta)             |
| anything else | **the run fails**           |

Anything that matches neither shape is a hard error, so a typo like `2.3.O`
or `2.3` fails loudly instead of quietly shipping as the wrong kind.

Two things to know:

- **`bN` means digits, and it is the same rule in all three repositories.**
  This integration is not on PyPI and could technically embed a sha, but it
  follows the same rule anyway - one rule beats an exception nobody
  remembers. In the two PyPI repositories the rule is load-bearing: PEP 440's
  pre-release segment is `b` followed by a *number*, and a sha would fail
  the upload *after* the tag and GitHub release already existed.
- **Betas do not need a CHANGELOG entry**; full releases still do. Betas get
  a generated stub instead, so a quick `bN` build does not need a changelog
  edit to get out the door.

Releases used to be cut `--prerelease` unconditionally, on the reasoning
that nothing here had reached a "stable" designation. That cost more than it
bought: **HACS hides prereleases** unless the user ticks the beta box
per-repository, so the normal install path saw no versions at all. All 25
pre-existing releases were converted to full releases when this changed.

Keep that in mind alongside the release-resolution rule above: a user with
the beta box ticked sees prereleases too, so prerelease is not a reliable
way to hide something from HACS - only keeping it out of the release list
is.

On a matching push, the workflow:

1. Reads the new version out of `manifest.json`.
2. Checks whether a tag for that version already exists (`git ls-remote
   --tags`) - if so, does nothing.
3. Extracts that version's own section out of
   `custom_components/cync_lan/CHANGELOG.md` (the `### <version>` heading
   must match `manifest.json` *exactly*, or this step fails loudly rather
   than silently publishing an empty/wrong release). Betas are exempt.
4. Tags the current commit and creates a GitHub Release from it via `gh
   release create`, passing `--latest` or `--prerelease` per the table
   above.

It is also `workflow_dispatch`-triggerable from the Actions tab, for a
manual re-run (e.g. if a push happened before `manifest.json` finished
propagating, or to retry after a transient failure) without needing an empty
commit.
