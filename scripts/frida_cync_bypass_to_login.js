/*
 * Frida Script: Dynamic Interception & Login View Navigator
 * Target Process: Cync (PlayCover container)
 */

console.log("[+] Frida Cync Login Bypass Script Active!");

var p_objc_getClass = Module.findExportByName(null, "objc_getClass");
var p_objc_msgSend = Module.findExportByName(null, "objc_msgSend");

if (p_objc_getClass && p_objc_msgSend) {
    console.log("[+] Found Objective-C Runtime exports in process memory!");

    var objc_getClass = new NativeFunction(p_objc_getClass, 'pointer', ['pointer']);
    var objc_msgSend = new NativeFunction(p_objc_msgSend, 'pointer', ['pointer', 'pointer']);

    // Query UIApplication class pointer
    var class_UIApplication = objc_getClass(Memory.allocUtf8String("UIApplication"));
    var sel_sharedApplication = Module.findExportByName(null, "sel_registerName");
    var sel_reg = new NativeFunction(sel_sharedApplication, 'pointer', ['pointer']);
    var sel_sharedApp = sel_reg(Memory.allocUtf8String("sharedApplication"));

    if (class_UIApplication && sel_sharedApp) {
        var appInstance = objc_msgSend(class_UIApplication, sel_sharedApp);
        console.log("[+] Obtained UIApplication sharedApplication instance: " + appInstance);
    }
}
