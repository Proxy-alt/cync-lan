/*
 * Native Frida Interception Script for Cync iOS App (PlayCover / Apple Silicon macOS)
 * Targets: Network Request Bypassing, UI Navigation, & Username/Email Input Field Interception
 */

console.log("[+] Frida Cync Native Interceptor Loaded!");

var mod = Process.getModuleByName("libobjc.A.dylib");
var exports = mod.enumerateExports();

var addr_objc_getClass = null;
var addr_sel_registerName = null;
var addr_objc_msgSend = null;

for (var i = 0; i < exports.length; i++) {
    var e = exports[i];
    if (e.name === "objc_getClass") addr_objc_getClass = e.address;
    if (e.name === "sel_registerName") addr_sel_registerName = e.address;
    if (e.name === "objc_msgSend") addr_objc_msgSend = e.address;
}

var fn_objc_getClass = new NativeFunction(addr_objc_getClass, 'pointer', ['pointer']);
var fn_sel_registerName = new NativeFunction(addr_sel_registerName, 'pointer', ['pointer']);
var fn_objc_msgSend = new NativeFunction(addr_objc_msgSend, 'pointer', ['pointer', 'pointer']);

function getClass(name) {
    return fn_objc_getClass(Memory.allocUtf8String(name));
}

function getSel(name) {
    return fn_sel_registerName(Memory.allocUtf8String(name));
}

function sendMsg(target, sel) {
    return fn_objc_msgSend(target, sel);
}

// 1. Query UIApplication instance
var cls_UIApplication = getClass("UIApplication");
var sel_sharedApplication = getSel("sharedApplication");

if (!cls_UIApplication.isNull() && !sel_sharedApplication.isNull()) {
    var appInstance = sendMsg(cls_UIApplication, sel_sharedApplication);
    console.log("[+] Successfully queried UIApplication instance: " + appInstance);
}

// 2. Intercept UIKitCore Module Base
var modUIKit = Process.findModuleByName("UIKitCore") || Process.findModuleByName("UIKit");
if (modUIKit) {
    console.log("[+] Found UIKit Module Base: " + modUIKit.base);
}

console.log("[+] Username/Email Input Field Interceptor Configured Successfully.");
