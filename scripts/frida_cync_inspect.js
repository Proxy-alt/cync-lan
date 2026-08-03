/*
 * Frida Dynamic Analysis & Interception Script for Cync iOS App
 * Target: HomeSetupImageListViewModel, ImageDownloadService, ImageCache, and Network Hangs
 */

console.log("[+] Cync iOS Frida Dynamic Analysis Script Loaded!");

if (ObjC.available) {
    console.log("[+] Objective-C Runtime Available");

    // 1. Intercept Network Requests (NSURLSession) causing gallery image load freeze
    var NSURLSession = ObjC.classes.NSURLSession;
    if (NSURLSession) {
        var dataTaskWithRequest = NSURLSession["- dataTaskWithRequest:completionHandler:"];
        if (dataTaskWithRequest) {
            Interceptor.attach(dataTaskWithRequest.implementation, {
                onEnter: function (args) {
                    var request = new ObjC.Object(args[2]);
                    var url = request.URL().absoluteString().toString();
                    if (url.indexOf("gecbyge") !== -1 || url.indexOf("image") !== -1 || url.indexOf("photo") !== -1) {
                        console.log("[+] [Network Intercept] App Request URL: " + url);
                    }
                }
            });
            console.log("[+] Hooked NSURLSession dataTaskWithRequest:completionHandler:");
        }
    }

    // 2. Intercept Home Setup & Gallery Photo View Models
    var classesToMonitor = [
        "HomeSetupImageListViewModel",
        "HomeSetupPreviewPhotoViewModel",
        "ImageDownloadService",
        "ImageCache",
        "TrueImageCheckImageProcessor"
    ];

    classesToMonitor.forEach(function (clsName) {
        var targetCls = ObjC.classes["_TtC4Cync" + clsName.length + clsName] || ObjC.classes["Cync." + clsName];
        if (targetCls) {
            console.log("[+] Successfully bound target class: " + clsName);
        }
    });

} else {
    console.log("[-] Objective-C Runtime Not Available");
}
