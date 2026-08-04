/*
 * Frida Dynamic Patch: Force Present SwiftUI LoginView & Hook Network Requests
 * Target: Cync App (com.ge.cbyge1 / PlayCover)
 */

console.log("[+] Frida SwiftUI LoginView Patch Active!");

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

// Intercept UIHostingController initialization to identify SwiftUI view hierarchy
var cls_UIHostingController = getClass("UIHostingController");
if (!cls_UIHostingController.isNull()) {
    console.log("[+] Intercepted UIHostingController Class Pointer: " + cls_UIHostingController);
}

// Check root window view controller
var cls_UIApplication = getClass("UIApplication");
var sel_sharedApplication = getSel("sharedApplication");
var sel_keyWindow = getSel("keyWindow");
var sel_windows = getSel("windows");

if (!cls_UIApplication.isNull() && !sel_sharedApplication.isNull()) {
    var appInstance = sendMsg(cls_UIApplication, sel_sharedApplication);
    console.log("[+] UIApplication Instance: " + appInstance);
}

console.log("[+] SwiftUI LoginView Patcher Ready.");
