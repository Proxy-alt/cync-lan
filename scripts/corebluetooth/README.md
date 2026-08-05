# CoreBluetooth CCCD probe

Answers the question Linux cannot answer about itself: when a client tries to
subscribe to this firmware's notify characteristic, is the connection lost
because the device demands it, or because BlueZ decides so?

Native CoreBluetooth in Swift rather than bleak, deliberately — this is the
same API the vendor's iOS app uses, so no translation layer can be blamed for
the result. Needs no mesh credentials.

```bash
./build.sh
open -W --stdout "$PWD/run.out" --stderr "$PWD/run.err" ./CyncCCCDTest.app
cat run.out
```

**Both steps matter.** CoreBluetooth aborts any process whose bundle lacks
`NSBluetoothAlwaysUsageDescription` — `SIGABRT`, no traceback, no output, which
is why a plain Python script cannot do this. And the bundle has to be launched
through LaunchServices: running the binary inside `Contents/MacOS/` directly
still aborts, because TCC only attributes the bundle on the `open` path.

Result: see `../../findings/ble_no_cccd_exists_at_all.md`. The characteristic
declares `notify` and ships no CCCD at all.
