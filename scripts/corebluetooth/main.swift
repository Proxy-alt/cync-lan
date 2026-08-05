// Does a refused CCCD write take the link down on CoreBluetooth?
//
// The one question Linux cannot answer about itself. On BlueZ, subscribing
// writes the Client Characteristic Configuration Descriptor, this firmware
// refuses it, and BlueZ then destroys the connection after its own ~30s
// timeout, reporting LOCAL_HOST_TERMINATED. The device never asked for that.
//
// CoreBluetooth also writes the descriptor - `setNotifyValue` has no
// local-only mode, unlike Android's `setCharacteristicNotification`. So if the
// link here SURVIVES the refusal, the teardown is BlueZ policy rather than an
// unavoidable consequence, which is a far stronger claim than the current
// evidence supports.
//
// Written in Swift rather than through bleak on purpose: this is CoreBluetooth
// natively, the same API the vendor's own iOS app uses, with no translation
// layer to be blamed for the result.
//
// Needs no mesh credentials. Only the GATT layer is involved.

import CoreBluetooth
import Foundation

// Telink's registered Bluetooth SIG company ID, little-endian on the wire.
// macOS hides BD_ADDRs behind opaque UUIDs, so this is the only way to pick
// Cync nodes out of a scan - the OUI filtering used on Linux is unavailable.
let telinkCompany: [UInt8] = [0x11, 0x02]

let pairingService = CBUUID(string: "00010203-0405-0607-0809-0A0B0C0D1910")
let notifyChar = CBUUID(string: "00010203-0405-0607-0809-0A0B0C0D1911")

let scanSeconds = 10.0
let holdSeconds = 60.0

final class Probe: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var central: CBCentralManager!
    var target: CBPeripheral?
    var connectedAt: Date?
    var subscribeResolvedAt: Date?
    var notifications = 0
    var finished = false

    func run() {
        central = CBCentralManager(delegate: self, queue: nil)
        RunLoop.main.run()
    }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        switch c.state {
        case .poweredOn:
            print("[*] Bluetooth on, scanning \(Int(scanSeconds))s for Telink nodes...")
            c.scanForPeripherals(withServices: nil, options: nil)
            DispatchQueue.main.asyncAfter(deadline: .now() + scanSeconds) {
                if self.target == nil {
                    print("[-] No Cync/Telink node seen. Move closer and re-run.")
                    exit(2)
                }
            }
        case .unauthorized:
            print("[-] Bluetooth permission denied for this app.")
            exit(3)
        case .poweredOff:
            print("[-] Bluetooth is off.")
            exit(4)
        default:
            break
        }
    }

    func centralManager(
        _ c: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        guard target == nil else { return }
        guard let data = advertisementData[CBAdvertisementDataManufacturerDataKey] as? Data,
              data.count >= 2,
              Array(data.prefix(2)) == telinkCompany
        else { return }

        target = peripheral
        c.stopScan()
        print("[+] Found \(peripheral.identifier) rssi \(RSSI), connecting...")
        peripheral.delegate = self
        c.connect(peripheral, options: nil)
    }

    func centralManager(_ c: CBCentralManager, didConnect peripheral: CBPeripheral) {
        connectedAt = Date()
        print("[+] Connected. Discovering the vendor service...")
        peripheral.discoverServices([pairingService])
    }

    func centralManager(
        _ c: CBCentralManager,
        didFailToConnect peripheral: CBPeripheral,
        error: Error?
    ) {
        print("[-] Connect failed: \(error?.localizedDescription ?? "unknown")")
        exit(5)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        guard let service = p.services?.first(where: { $0.uuid == pairingService }) else {
            print("[-] Vendor service not found: \(error?.localizedDescription ?? "absent")")
            exit(6)
        }
        p.discoverCharacteristics([notifyChar], for: service)
    }

    func peripheral(
        _ p: CBPeripheral,
        didDiscoverCharacteristicsFor service: CBService,
        error: Error?
    ) {
        guard let char = service.characteristics?.first(where: { $0.uuid == notifyChar })
        else {
            print("[-] Notify characteristic not found.")
            exit(7)
        }
        // Does the CCCD even exist? CoreBluetooth's refusal reason turned out
        // to be "attribute could not be found", which is a different claim from
        // BlueZ's WRITE_NOT_PERMITTED - it suggests the descriptor may simply
        // be absent rather than present-and-refusing. Discover descriptors and
        // say so outright, because the two readings mean very different things.
        print("[*] Properties: \(describe(char.properties))")
        p.discoverDescriptors(for: char)

        print("[*] Writing the vendor enable byte (a plain value write, not the CCCD)")
        p.writeValue(Data([0x01]), for: char, type: .withResponse)

        print("[*] Calling setNotifyValue - THIS is the CCCD write")
        subscribeResolvedAt = nil
        p.setNotifyValue(true, for: char)

        // Whatever the descriptor write does, hold the link and see whether it
        // survives. This is the measurement.
        DispatchQueue.main.asyncAfter(deadline: .now() + holdSeconds) {
            guard !self.finished else { return }
            self.finished = true
            let alive = p.state == .connected
            print("")
            print(String(repeating: "=", count: 58))
            if alive {
                print("*** LINK SURVIVED \(Int(holdSeconds))s after the CCCD write.")
                print("    BlueZ tears the link down ~30s after the same refusal,")
                print("    so that teardown is BlueZ policy, not the device.")
            } else {
                print("*** LINK DROPPED - same as BlueZ.")
            }
            print("    notifications received: \(self.notifications)")
            print(String(repeating: "=", count: 58))
            exit(alive ? 0 : 1)
        }
    }

    func peripheral(
        _ p: CBPeripheral,
        didDiscoverDescriptorsFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        let found = characteristic.descriptors ?? []
        if found.isEmpty {
            print("[!] NO DESCRIPTORS on the notify characteristic - there is no")
            print("    CCCD (0x2902) to write. Subscribing cannot work by the book.")
        } else {
            for d in found { print("[*] descriptor present: \(d.uuid)") }
        }
    }

    func peripheral(
        _ p: CBPeripheral,
        didUpdateNotificationStateFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        let elapsed = connectedAt.map { Date().timeIntervalSince($0) } ?? 0
        if let error {
            print(String(format: "[!] setNotifyValue REFUSED after %.2fs: %@",
                         elapsed, error.localizedDescription))
        } else {
            print(String(format: "[+] setNotifyValue ACCEPTED after %.2fs (isNotifying=%@)",
                         elapsed, characteristic.isNotifying ? "true" : "false"))
        }
    }

    func peripheral(
        _ p: CBPeripheral,
        didUpdateValueFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        notifications += 1
        if notifications <= 3, let v = characteristic.value {
            print("[+] notification \(notifications): \(v.map { String(format: "%02x", $0) }.joined())")
        }
    }

    func peripheral(_ p: CBPeripheral, didWriteValueFor c: CBCharacteristic, error: Error?) {
        if let error {
            print("[!] vendor enable-write failed: \(error.localizedDescription)")
        } else {
            print("[+] vendor enable-write accepted")
        }
    }

    func centralManager(
        _ c: CBCentralManager,
        didDisconnectPeripheral peripheral: CBPeripheral,
        error: Error?
    ) {
        guard !finished else { return }
        finished = true
        let elapsed = connectedAt.map { Date().timeIntervalSince($0) } ?? 0
        print("")
        print(String(repeating: "=", count: 58))
        print(String(format: "*** LINK DROPPED after %.1fs: %@",
                     elapsed, error?.localizedDescription ?? "clean close"))
        print("    notifications received: \(notifications)")
        print(String(repeating: "=", count: 58))
        exit(1)
    }
}

func describe(_ p: CBCharacteristicProperties) -> String {
    var out: [String] = []
    if p.contains(.read) { out.append("read") }
    if p.contains(.write) { out.append("write") }
    if p.contains(.writeWithoutResponse) { out.append("writeNoResp") }
    if p.contains(.notify) { out.append("notify") }
    if p.contains(.indicate) { out.append("indicate") }
    return out.joined(separator: ",")
}

setbuf(stdout, nil)
Probe().run()
