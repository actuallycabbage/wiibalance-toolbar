import Foundation
import FlatBuffers

struct Adapter: Decodable, Identifiable, Equatable {
    let transport: String
    let name: String
    let manufacturer: String?
    let serial: String?

    var id: String { transport }
}

struct WiiDevice: Decodable, Identifiable, Equatable {
    let address: String
    let name: String
    let kind: String
    let paired: Bool
    let connected: Bool

    var id: String { address }
    var isBalanceBoard: Bool { kind == "balance_board" }
}

struct BalanceSample: Decodable, Equatable {
    var x: Float
    var y: Float
    var address: String?
    var weightKg: Double?
    var batteryPercent: Int?
    var batteryBars: Int?

    var unitX: Double { Double(min(max(x, -100), 100) + 100) / 200 }
    // The board reports positive Y towards the front; Canvas coordinates grow downwards.
    var unitY: Double { Double(100 - min(max(y, -100), 100)) / 200 }

    var isValid: Bool {
        x.isFinite && y.isFinite &&
        (weightKg.map { $0.isFinite && $0 >= 0 } ?? true) &&
        (batteryPercent.map { (0...100).contains($0) } ?? true) &&
        (batteryBars.map { (0...4).contains($0) } ?? true)
    }
}

struct ServerReady: Decodable {
    let protocolVersion: Int
    let capabilities: [String]
}

struct RadioStatus: Decodable {
    enum State: String, Decodable { case stopped, starting, listening }
    let state: State
    let transport: String?
}

struct PairingStatus: Decodable {
    enum State: String, Decodable { case idle, scanning, pairing, failed }
    let state: State
    let address: String?
    let message: String?

    var isBusy: Bool { state == .scanning || state == .pairing }
}

enum ServerEvent: Decodable {
    case ready(ServerReady)
    case status(RadioStatus)
    case adapters([Adapter])
    case devices([WiiDevice])
    case balance(BalanceSample)
    case pairing(PairingStatus)
    case response(id: Int, ok: Bool, error: String?)
    case error(String)
    case unknown

    private enum Keys: String, CodingKey { case type, event, data, id, ok, error }
    private struct AdapterList: Decodable { let adapters: [Adapter] }
    private struct DeviceList: Decodable { let devices: [WiiDevice] }
    private struct ErrorMessage: Decodable { let message: String }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: Keys.self)
        switch try values.decode(String.self, forKey: .type) {
        case "response":
            self = .response(
                id: try values.decode(Int.self, forKey: .id),
                ok: try values.decode(Bool.self, forKey: .ok),
                error: try values.decodeIfPresent(String.self, forKey: .error)
            )
        case "event":
            switch try values.decode(String.self, forKey: .event) {
            case "ready": self = .ready(try values.decode(ServerReady.self, forKey: .data))
            case "server_status": self = .status(try values.decode(RadioStatus.self, forKey: .data))
            case "adapters": self = .adapters(try values.decode(AdapterList.self, forKey: .data).adapters)
            case "devices": self = .devices(try values.decode(DeviceList.self, forKey: .data).devices)
            case "balance": self = .balance(try values.decode(BalanceSample.self, forKey: .data))
            case "pairing": self = .pairing(try values.decode(PairingStatus.self, forKey: .data))
            case "error": self = .error(try values.decode(ErrorMessage.self, forKey: .data).message)
            default: self = .unknown
            }
        default: self = .unknown
        }
    }
}

struct ServerCommand: Encodable {
    let id: Int
    let method: String
    var params: [String: String] = [:]

    func encoded() throws -> Data {
        var data = try JSONEncoder().encode(self)
        data.append(0x0A)
        // Only one command is in flight. Keep it below pipe capacity so a helper
        // which stops reading stdin cannot block the UI on a large write.
        guard data.count <= 4_096 else { throw WireError.invalid("Server command exceeds 4 KiB.") }
        return data
    }
}

enum WireError: LocalizedError {
    case invalid(String)
    var errorDescription: String? {
        switch self { case .invalid(let message): return message }
    }
}

/// A process uses either NDJSON control/events or the original FlatBuffers stream.
/// It cannot switch formats midway through a session.
struct ServerStreamDecoder {
    enum Format { case json, legacy }
    private(set) var format: Format?
    private var buffer = Data()
    private let jsonDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    mutating func append(_ data: Data) throws -> [ServerEvent] {
        buffer.append(data)
        var events: [ServerEvent] = []
        if format == nil, let first = buffer.first {
            // Legacy lengths are bounded to 1 MiB, so their first byte is always zero.
            // NDJSON must start with '{'; banners and logs belong on stderr.
            switch first {
            case 0x7B: format = .json
            case 0: format = .legacy
            default: throw WireError.invalid("Unrecognised server output. Send protocol messages on stdout and logs on stderr.")
            }
        }

        if format == .json {
            while let newline = buffer.firstIndex(of: 0x0A) {
                let line = buffer.prefix(upTo: newline)
                guard line.count <= 65_536 else { throw WireError.invalid("Server JSON message exceeds 64 KiB.") }
                if !line.isEmpty {
                    events.append(try jsonDecoder.decode(ServerEvent.self, from: line))
                }
                buffer.removeSubrange(...newline)
            }
            guard buffer.count <= 65_536 else { throw WireError.invalid("Unterminated server JSON message exceeds 64 KiB.") }
        } else if format == .legacy {
            while buffer.count >= 4 {
                let length = buffer.prefix(4).reduce(0) { ($0 << 8) | Int($1) }
                guard (1...1_048_576).contains(length) else { throw WireError.invalid("Invalid FlatBuffers frame length: \(length).") }
                guard buffer.count >= length + 4 else { break }
                var bytes = ByteBuffer(bytes: Array(buffer.dropFirst(4).prefix(length)))
                var verifier = try Verifier(buffer: &bytes)
                try ForwardOffset<WiimoteServer_ServerEnvelope>.verify(
                    &verifier, at: 0, of: WiimoteServer_ServerEnvelope.self
                )
                let envelope: WiimoteServer_ServerEnvelope = getRoot(byteBuffer: &bytes)
                if envelope.messageType == .balanceframe,
                   let frame = envelope.message(type: WiimoteServer_BalanceFrame.self) {
                    events.append(.balance(BalanceSample(x: frame.x, y: frame.y)))
                }
                buffer.removeFirst(length + 4)
            }
        }
        return events
    }

    func finish() throws {
        guard buffer.isEmpty else { throw WireError.invalid("Server exited with an incomplete protocol message.") }
    }
}
