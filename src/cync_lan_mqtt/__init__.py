"""cync-lan-mqtt: Docker/MQTT add-on built on the cync-lan core library.

`__version__` is read from installed package metadata rather than written out
here. It was a hand-maintained literal and had drifted - still 0.1.1 at release
0.2.2 - for the same reason the core library's had: nothing in the release
process touches a string that only exists in source.

Note that the version users actually see comes from the core library, not from
here: `const.CYNC_VERSION` is imported from `cync_lan`, and that is what the
startup log line, `cync-lan -V` and the MQTT `sw_version` all report. Whether
this daemon ought to report its own version rather than the library's is a
separate question, and deliberately not changed here.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    __version__: str = _distribution_version("cync-lan-mqtt")
except PackageNotFoundError:
    # Importable from a source tree that was never pip-installed. Better to be
    # obviously unknown than to invent a number that looks real.
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
