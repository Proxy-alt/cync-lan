"""Fixtures shared across `cync-lan` core's test suite.

Unlike the Home Assistant integration's own test suite (a separate
project, `tests/components/cync_lan/` on `feature/ha-custom-component`),
this one has no Home Assistant dependency at all - these tests exercise
`cync_lan`'s protocol/device/cloud-auth code directly.
"""

from __future__ import annotations
