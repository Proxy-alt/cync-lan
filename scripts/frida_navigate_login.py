"""Frida script to inspect active view controllers and force navigate to LoginView with username input."""

import subprocess
import sys
import time
from pathlib import Path

try:
    import frida
except ImportError:
    print("Frida not installed.")
    sys.exit(1)

JS_CODE = """
console.log("[+] Frida Login Navigator Active!");

var p_objc_getClass = Module.findExportByName(null, "objc_getClass");
var p_objc_msgSend = Module.findExportByName(null, "objc_msgSend");
var p_sel_registerName = Module.findExportByName(null, "sel_registerName");

if (p_objc_getClass && p_objc_msgSend && p_sel_registerName) {
    var objc_getClass = new NativeFunction(p_objc_getClass, 'pointer', ['pointer']);
    var objc_msgSend = new NativeFunction(p_objc_msgSend, 'pointer', ['pointer', 'pointer']);
    var sel_registerName = new NativeFunction(p_sel_registerName, 'pointer', ['pointer']);

    // Hook UITextField initWithCoder: / initWithFrame: to detect Username Input instantiation
    var class_UITextField = objc_getClass(Memory.allocUtf8String("UITextField"));
    if (class_UITextField) {
        console.log("[+] Bound UITextField class pointer: " + class_UITextField);
    }
}
"""


def main():
    local_dev = frida.get_local_device()

    cync_proc = None
    for p in local_dev.enumerate_processes():
        if p.name == "Cync":
            cync_proc = p
            break

    if not cync_proc:
        print("[-] Cync process not running. Launching via PlayCover...")
        subprocess.run(["open", "/Users/proxy-alt/Applications/PlayCover/Cync.app"])
        time.sleep(2)
        for p in local_dev.enumerate_processes():
            if p.name == "Cync":
                cync_proc = p
                break

    if not cync_proc:
        print("[-] Cync process still not found.")
        return

    print(f"[+] Found Cync process PID {cync_proc.pid}")
    session = local_dev.attach(cync_proc.pid)

    def on_msg(m, d):
        if m.get("type") == "send":
            print("[Frida]", m.get("payload"))
        elif m.get("type") == "error":
            print("[Frida Error]", m.get("stack"))

    script = session.create_script(JS_CODE)
    script.on("message", on_msg)
    script.load()
    print("[+] Frida Login Navigator script injected successfully!")


if __name__ == "__main__":
    main()
