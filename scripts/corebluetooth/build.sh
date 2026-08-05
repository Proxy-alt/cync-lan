#!/usr/bin/env bash
# Build CyncCCCDTest.app.
#
# The bundle exists for one reason: CoreBluetooth aborts any process whose
# bundle lacks NSBluetoothAlwaysUsageDescription (SIGABRT, no Python
# traceback, nothing in stdout). That is why this is a Swift app rather than
# a bleak script - a plain interpreter cannot carry the key.
set -euo pipefail
cd "$(dirname "$0")"
APP="CyncCCCDTest.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>CyncCCCDTest</string>
  <key>CFBundleExecutable</key><string>CyncCCCDTest</string>
  <key>CFBundleIdentifier</key><string>local.cync.cccdtest</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSBluetoothAlwaysUsageDescription</key>
  <string>Checks whether a Cync light keeps its Bluetooth connection after refusing a notification subscription.</string>
</dict>
</plist>
PLIST

swiftc -O -o "$APP/Contents/MacOS/CyncCCCDTest" main.swift \
  -framework CoreBluetooth -framework Foundation
codesign --force --sign - "$APP" 2>/dev/null || true
echo "built $APP"
