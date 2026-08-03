"""Launch Cync app via URL scheme / PlayCover and attach Frida login bypass script."""

import subprocess
import sys
import time
from pathlib import Path

try:
    import frida
except ImportError:
    print("Frida not installed.")
    sys.exit(1)


def on_message(message, data):
    if message["type"] == "send":
        print(f"[Frida Send] {message['payload']}")
    elif message["type"] == "error":
        print(f"[Frida Error] {message['stack']}")
    else:
        print(f"[Frida Message] {message}")


def main():
    script_path = Path(__file__).parent / "frida_cync_bypass_to_login.js"
    js_code = script_path.read_text(encoding="utf-8")

    # Step 1: Open app via cync:// deep link or PlayCover wrapper
    print("[+] Launching Cync app via URL scheme / PlayCover...")
    subprocess.run(["open", "cync://"], capture_output=True)
    subprocess.run(["open", "/Users/proxy-alt/Applications/PlayCover/Cync.app"], capture_output=True)
    time.sleep(2)

    # Step 2: Find running local process
    local_dev = frida.get_local_device()
    cync_proc = None
    for p in local_dev.enumerate_processes():
        if p.name == "Cync" or p.name == "com.ge.cbyge1":
            cync_proc = p
            break

    if not cync_proc:
        print("[-] Target Cync process not found yet. Enumerating running processes...")
        for p in local_dev.enumerate_processes()[:15]:
            print(f"  [{p.pid}] {p.name}")
        return

    print(f"[+] Found Cync target process: PID {cync_proc.pid} ({cync_proc.name})")

    # Step 3: Attach Frida session and load script
    session = local_dev.attach(cync_proc.pid)
    script = session.create_script(js_code)
    script.on("message", on_message)
    script.load()
    print("[+] Frida login bypass script injected successfully!")
    print("[+] Monitoring Cync app events... Press Ctrl+C to exit.\n")
    sys.stdout.flush()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[+] Exiting Frida monitor.")


if __name__ == "__main__":
    main()
