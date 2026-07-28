"""cync-lan: local control of Cync / C by GE devices.

`__version__` is read from installed package metadata rather than written out
here. It was a hand-maintained literal, and it had drifted badly: it still said
0.1.2 at release 0.5.2, a version that was never published at all.

That number is not decorative. `const.CYNC_VERSION` is built from it, and it
reaches users in three places - the startup line that the bug report template
asks people to paste, the output of `cync-lan -V`, and the `sw_version` shown
on every device page in Home Assistant. Every one of them was reporting a
fiction, which quietly undermines any bug report that depends on knowing what
someone is running.

Deriving it from the distribution metadata means pyproject.toml is the single
place a version is declared, and this cannot fall behind it again.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    __version__: str = _distribution_version("cync-lan")
except PackageNotFoundError:
    # Importable from a source tree that was never pip-installed - a checkout
    # run directly, or the test suite before an editable install. Better to be
    # obviously unknown than to invent a number that looks real.
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
