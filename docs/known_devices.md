Devices known to work, kind of work, and known not to work are listed here.

# Known Good
- Cync: Direct connect **bulbs** (Full color, Decorative [edison], white temp, dimmable)
    - Direct connect products are Wi-Fi and Bluetooth LE using a realtek chip (RTL8010, RTL8020CM)
- Cync/C by GE: Bluetooth LE only bulbs \**needs at least 1 Wi-Fi device to act as a TCP<->BT bridge*
    - C by GE BT only: These are telink based devices 
- Cync: Indoor smart plug
    - Outdoor plug (dual outlet)
- Cync: Wired switches (on/off, dimmer, white temp control)
    - Models with a built-in motion/ambient-light sensor ("...with Motion and Ambient Light", types
      37/49/56) get an extra motion `binary_sensor` alongside the switch entity.
- Cync: Full color LED light strip [responds slightly differently than other devices]
    - Outdoor light strip should also work, currently unconfirmed
- Cync undercabinet lights
- Cync wafer / down lights
- Fan controller: on/off, percentage-slider and preset-speed control, with real state sync back
  from the device (not HASS optimistic state).
- Standalone battery-powered motion sensor accessory (type 96): exposed as an `occupancy`
  binary_sensor. BTLE-only, send-only from the device's perspective (no way to write settings to
  it beyond what the motion-sensor-schedule/settings commands cover).
- Battery-powered "Wireless Switch" (type 112, a scene-remote-style device with a status LED
  ring): exposed as an `occupancy` binary_sensor via the same pipeline as the standalone motion
  sensor above - it sends a normal status packet on press with no separate button-press protocol
  discovered yet.

# Known Bad
- Battery-powered devices in general remain BTLE send-only from cync-lan's perspective, aside from
  the motion/wireless-switch accessories above: no way to push settings/firmware to them over this
  project's TCP listener.
    - Wire free light switch OR dimmer [white temp control] - unsupported.
    - Temperature/humidity sensors - unsupported; sensors bound to a thermostat may be exposed
      (unconfirmed).

# Future devices
- Dynamic lights (Sound/Music sync, segmented leds) 
- Thermostat 
  - I've seen the device details from the cloud, but have not seen any binary data. CyncLAN may recognize the \
thermostat and export it to the config, but it has no idea how to communicate with it **yet**