Thanks for the detailed pushback — that's a fair objection, and I agree unconditional discovery is the wrong trade for every other device on the bus.

I've written the lazy-verify version instead: `discover_descs()` still synthesizes the `0x2902` exactly as it does today, but `register_notify()` now issues a single-handle `FIND_INFORMATION` for that handle before it ever writes to it, and only proceeds with the CCC write if the peer confirms `0x2902` there. If the peer answers with anything else (or doesn't answer), `ccc_handle` is cleared and this reaches the same "no CCC" path `register_notify()` already handles correctly today. That's the extra round trip only on first `register_notify()` for a characteristic whose CCC was never actually discovered — a real, discovered CCC is untouched by this patch and costs nothing extra.

Patch attached: `0002-lazy-verify-ccc-before-writing.patch`.

I have not built or run this one either — same situation as before, I have the affected hardware but no BlueZ build environment on it. I understand `tools/btgatt-client` isn't the right way to exercise this, since it doesn't go through `bluetoothd`'s discovery path — happy to test through `bluetoothd` directly if you can point me at what you'd want to see (a debug log, a specific trace), or if you'd rather test it yourself against the traces already attached to the issue.
