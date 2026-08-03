"""Frida Dynamic Analysis Runner for Cync iOS App."""

import sys
import time
from pathlib import Path

try:
    import frida
except ImportError:
    print("Frida not installed. Install via `pip install frida-tools`")
    sys.exit(1)


def on_message(message, data):
    if message["type"] == "send":
        print(f"[Frida Send] {message['payload']}")
    elif message["type"] == "error":
        print(f"[Frida Error] {message['stack']}")
    else:
        print(f"[Frida Message] {message}")


def main():
    script_path = Path(__file__).parent / "frida_cync_inspect.js"
    js_code = script_path.read_text(encoding="utf-8")

    dm = frida.get_device_manager()
    print("Enumerate Frida Devices:")
    devices = dm.enumerate_devices()
    for d in devices:
        print(f" - {d.id} ({d.name}, type={d.type})")

    # Try attaching to connected USB/Remote iOS device or local process
    device = None
    for d in devices:
        if d.type in ("usb", "remote") and "iOS" in d.name:
            device = d
            break
    if not device:
        device = frida.get_local_device()

    print(f"\nAttaching Frida script to target device: {device.name} [{device.id}]...")

    try:
        session = device.attach("Cync")
        script = session.create_script(js_code)
        script.on("message", on_message)
        script.load()
        print("\nFrida script injected successfully! Monitoring Cync app events...")
        print("Press Ctrl+C to stop.\n")
        sys.stdout.flush()
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"\nFailed to attach to target process: {e}")
        print("Ensure the Cync app is running or Frida server is active on the target device.")


if __name__ == "__main__":
    main()
