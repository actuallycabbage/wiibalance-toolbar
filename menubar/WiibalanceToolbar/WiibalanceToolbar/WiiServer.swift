import Foundation
import Combine
import Observation

@MainActor
@Observable
final class WiiServer {
    enum Phase { case stopped, starting, legacy, ready, stopping, failed }
    enum StatusTone { case inactive, busy, connected, error }
    enum MenuBarState { case stopped, waiting, balance }
    private enum LegacyStatus { case unknown, retrying, connected }

    private(set) var phase: Phase = .stopped
    private(set) var capabilities: Set<String> = []
    private(set) var adapters: [Adapter] = []
    private(set) var devices: [WiiDevice] = []
    private(set) var radio = RadioStatus(state: .stopped, transport: nil)
    private(set) var pairing = PairingStatus(state: .idle, address: nil, message: nil)
    private(set) var sample: BalanceSample?
    private(set) var sampleDate: Date?
    private(set) var isPopoverVisible = false
    private var hasReading = false
    private var legacyStatus: LegacyStatus = .unknown
    @ObservationIgnored private var diagnosticLineBuffer = ""
    private(set) var pendingMethod: String?
    private(set) var diagnostics = ""
    var lastError: String?
    var selectedTransport: String {
        didSet { UserDefaults.standard.set(selectedTransport, forKey: "selectedTransport") }
    }

    private var session: ServerProcess?
    @ObservationIgnored private var decoder = ServerStreamDecoder()
    private var nextID = 0
    private var pendingID: Int?
    @ObservationIgnored private var commandTimeout: Task<Void, Never>?
    @ObservationIgnored private var pairingTimeout: Task<Void, Never>?
    @ObservationIgnored private var startupTimeout: Task<Void, Never>?
    @ObservationIgnored private var displayTimer: AnyCancellable?
    // The newest wire reading is authoritative for the menu bar. The observable
    // sample/date above are a snapshot published only while the popover is visible.
    @ObservationIgnored private(set) var currentReading: (sample: BalanceSample, date: Date)?
    @ObservationIgnored private var stopCompletion: (() -> Void)?
    private var pairingRequested = false
    private var pairingCancellationRequested = false

    init() {
        selectedTransport = UserDefaults.standard.string(forKey: "selectedTransport") ?? ""
    }

    var isActive: Bool { session != nil }
    var canSend: Bool { phase == .ready && pendingID == nil }
    var activeBoard: WiiDevice? { devices.first { $0.isBalanceBoard && $0.connected } }
    var hasConnectedBoard: Bool { activeBoard != nil || (phase == .legacy && legacyStatus == .connected) }
    var menuBarState: MenuBarState {
        if phase == .stopped || phase == .stopping || phase == .failed { return .stopped }
        if phase == .ready && radio.state == .stopped && pendingMethod != "server_start" { return .stopped }
        if pairing.state == .failed { return .stopped }
        return currentReading == nil ? .waiting : .balance
    }
    var statusTone: StatusTone {
        if phase == .failed { return .error }
        if phase == .stopped { return .inactive }
        if phase == .starting || phase == .stopping || pendingMethod != nil || pairing.isBusy { return .busy }
        if pairing.state == .failed { return .error }
        return hasConnectedBoard ? .connected : .busy
    }
    var statusLabel: String {
        switch phase {
        case .stopped: return "Stopped"
        case .starting: return "Starting…"
        case .stopping: return "Stopping…"
        case .failed: return "Connection unavailable"
        case .legacy:
            switch legacyStatus {
            case .retrying: return "Retrying adapter…"
            case .connected: return hasReading ? "Connected" : "Connected · waiting for readings"
            case .unknown: return hasReading ? "Receiving readings" : "Waiting for server updates"
            }
        case .ready:
            if let pendingMethod {
                switch pendingMethod {
                case "server_start": return "Starting adapter…"
                case "server_stop": return "Stopping adapter…"
                case "wiimote_scan": return "Starting discovery…"
                case "wiimote_pair", "wiiboard_pair": return "Starting pairing…"
                case "wiimote_cancel_pairing": return "Cancelling pairing…"
                case "wiimote_disconnect": return "Disconnecting…"
                case "adapter_list": return "Checking adapters…"
                case "wiimote_list": return "Checking devices…"
                default: return "Updating connection…"
                }
            }
            if pairing.state == .scanning { return "Looking for your board…" }
            if pairing.state == .pairing { return "Pairing your board…" }
            if pairing.state == .failed { return "Pairing failed" }
            if activeBoard != nil { return hasReading ? "Connected" : "Connected · waiting for readings" }
            switch radio.state {
            case .stopped: return "Choose an adapter"
            case .starting: return "Starting adapter…"
            case .listening: return "Ready for a Balance Board"
            }
        }
    }

    func supports(_ method: String) -> Bool { capabilities.contains(method) }

    func start() {
        guard session == nil else { return }
        lastError = nil
        clearConnection()
        decoder = ServerStreamDecoder()
        let process = ServerProcess()
        do {
            let config = try AppSettings.launchConfiguration()
            session = process
            phase = .starting
            appendLog("Starting \(config.python.path) -m app.server.server\n")
            try process.start(
                executable: config.python, arguments: ["-m", "app.server.server", "--json"], directory: config.root,
                output: { [weak self, weak process] data in
                    guard let self, let process, self.session === process else { return }
                    self.receive(data)
                },
                diagnostic: { [weak self, weak process] data in
                    guard let self, let process, self.session === process else { return }
                    self.receiveDiagnostic(data)
                },
                failed: { [weak self, weak process] message in
                    guard let self, let process, self.session === process, self.phase != .stopping else { return }
                    self.fail(message)
                },
                exited: { [weak self, weak process] code in
                    guard let self, let process, self.session === process else { return }
                    self.didExit(code)
                }
            )
            // Python currently only reports changes in balance, so an idle legacy
            // process can be completely silent. Silence is not proof of connection.
            startupTimeout = Task { [weak self] in
                try? await Task.sleep(for: .seconds(3))
                guard !Task.isCancelled, let self, self.phase == .starting else { return }
                if self.decoder.format == .json {
                    self.fail("The NDJSON server did not send a ready event within 3 seconds.")
                    return
                }
                self.phase = .legacy
            }
            updateDisplayTimer()
        } catch {
            session = nil
            phase = .failed
            lastError = error.localizedDescription
            appendLog("Launch failed: \(error.localizedDescription)\n")
        }
    }

    func stop(completion: (() -> Void)? = nil) {
        pairingRequested = false
        pairingCancellationRequested = false
        guard let session else { completion?(); return }
        stopCompletion = completion
        lastError = nil
        phase = .stopping
        cancelTasks()
        clearConnection()
        session.stop()
    }

    func setPopoverVisible(_ visible: Bool) {
        guard isPopoverVisible != visible else { return }
        isPopoverVisible = visible
        updateDisplayTimer()
    }

    private func updateDisplayTimer() {
        displayTimer = nil
        guard isPopoverVisible, isActive else { return }
        // Catch up immediately on opening, then publish at 30 Hz only while visible.
        // Common mode keeps updates flowing while the popover is tracking input.
        flushSample()
        displayTimer = Timer.publish(every: 1.0 / 30, on: .main, in: .common)
            .autoconnect().sink { [weak self] _ in
                MainActor.assumeIsolated { self?.flushSample() }
            }
    }

    func request(_ method: String, params: [String: String] = [:]) {
        guard canSend, supports(method), let session else { return }
        nextID += 1
        let id = nextID
        do {
            try session.send(ServerCommand(id: id, method: method, params: params))
            pendingID = id
            pendingMethod = method
            lastError = nil
            commandTimeout = Task { [weak self] in
                try? await Task.sleep(for: .seconds(10))
                guard !Task.isCancelled, let self, self.pendingID == id else { return }
                // The helper may have acted without replying. Stop it rather than let
                // a retry race an operation whose outcome the UI cannot establish.
                self.fail("No acknowledgement for \(method). The server was stopped; check Diagnostics before retrying.")
            }
        } catch {
            fail("Could not send \(method): \(error.localizedDescription)")
        }
    }

    func beginPairing() {
        pairingRequested = true
        pairingCancellationRequested = false
        if !isActive { start() }
        if canSend && radio.state == .stopped {
            request("server_start", params: ["transport": selectedTransport.isEmpty ? "usb:0" : selectedTransport])
        }
        advancePairing()
    }

    func cancelPairing() {
        pairingRequested = false
        pairingCancellationRequested = true
        advancePairing()
    }

    private func advancePairing() {
        guard canSend else { return }
        if pairingCancellationRequested {
            pairingCancellationRequested = false
            if pairing.isBusy { request("wiimote_cancel_pairing") }
        } else if pairingRequested && !pairing.isBusy && supports("wiiboard_pair") {
            if hasConnectedBoard {
                pairingRequested = false
            } else if radio.state == .listening {
                pairingRequested = false
                request("wiiboard_pair")
            }
        }
    }

    private func receive(_ data: Data) {
        guard phase != .stopping else { return }
        do {
            for event in try decoder.append(data) { try apply(event) }
            if decoder.format == .legacy, phase == .starting { phase = .legacy }
        } catch {
            fail("Invalid server message: \(error.localizedDescription)")
        }
    }

    // Internal so the protocol state transitions can be tested without Bluetooth hardware.
    func apply(_ event: ServerEvent) throws {
        switch event {
        case .ready(let ready):
            guard ready.protocolVersion == 1 else { throw WireError.invalid("Unsupported server protocol \(ready.protocolVersion); expected 1.") }
            guard phase == .starting || phase == .legacy else { throw WireError.invalid("Unexpected duplicate server handshake.") }
            startupTimeout?.cancel()
            capabilities = Set(ready.capabilities)
            phase = .ready
            request("server_start", params: ["transport": selectedTransport.isEmpty ? "usb:0" : selectedTransport])
        case .response(let id, let ok, let error):
            guard id == pendingID else { return }
            commandTimeout?.cancel()
            pendingID = nil
            pendingMethod = nil
            if !ok {
                pairingRequested = false
                lastError = error ?? "The server rejected the command."
            }
        case .status(let status):
            try requireReady()
            radio = status
            if status.state == .stopped {
                clearSample()
                devices = []
                pairingTimeout?.cancel()
                pairing = PairingStatus(state: .idle, address: nil, message: nil)
            }
        case .adapters(let list):
            try requireReady()
            guard Set(list.map(\.id)).count == list.count else { throw WireError.invalid("Duplicate adapter identifiers.") }
            adapters = list
            if selectedTransport.isEmpty, let first = list.first { selectedTransport = first.transport }
        case .devices(let list):
            try requireReady()
            guard Set(list.map(\.id)).count == list.count else { throw WireError.invalid("Duplicate device addresses.") }
            let previousBoard = activeBoard?.address
            devices = list
            if previousBoard != activeBoard?.address || activeBoard == nil { clearSample() }
        case .balance(let value):
            if decoder.format == .json { try requireReady() }
            guard value.isValid else { throw WireError.invalid("Non-finite or invalid balance measurement.") }
            if phase == .ready {
                guard let address = value.address else { throw WireError.invalid("Balance event has no board address.") }
                guard radio.state == .listening, activeBoard?.address == address else { return }
            }
            currentReading = (value, Date())
            // Status text depends on whether a reading exists, not its coordinates.
            // Otherwise the header would still be invalidated by every balance update.
            if !hasReading { hasReading = true }
        case .pairing(let status):
            try requireReady()
            let previousState = pairing.state
            pairing = status
            if status.state != previousState { pairingTimeout?.cancel() }
            // Automatic discovery deliberately runs until Back/cancel. Once a board
            // is selected, pairing still has a bounded deadline, unaffected by messages.
            if status.state == .pairing && previousState != .pairing {
                pairingTimeout = Task { [weak self] in
                    try? await Task.sleep(for: .seconds(60))
                    guard !Task.isCancelled, let self, self.pairing.isBusy else { return }
                    self.fail("Pairing did not finish within 60 seconds. The server was stopped so you can retry.")
                }
            }
            if status.state == .failed { lastError = status.message ?? "Pairing failed. Press the red SYNC button and try again." }
        case .error(let message):
            pairingRequested = false
            lastError = message
        case .unknown: break
        }
        advancePairing()
    }

    private func requireReady() throws {
        guard phase == .ready else { throw WireError.invalid("The server must send its ready event before state events.") }
    }

    private func flushSample() {
        guard let (value, date) = currentReading, date != sampleDate else { return }
        sample = value
        sampleDate = date
    }

    private func fail(_ message: String) {
        stop()
        lastError = message
        appendLog(message + "\n")
    }

    private func didExit(_ code: Int32) {
        let requested = phase == .stopping
        if !requested {
            do { try decoder.finish() } catch { lastError = error.localizedDescription }
            if lastError == nil { lastError = "The connection stopped unexpectedly. See Diagnostics for details." }
        }
        appendLog("Python server exited (status \(code)).\n")
        session = nil
        cancelTasks()
        clearConnection()
        phase = requested && lastError == nil ? .stopped : .failed
        let completion = stopCompletion
        stopCompletion = nil
        completion?()
    }

    private func cancelTasks() {
        startupTimeout?.cancel()
        commandTimeout?.cancel()
        pairingTimeout?.cancel()
        displayTimer = nil
        pendingID = nil
        pendingMethod = nil
    }

    private func clearSample() {
        currentReading = nil
        hasReading = false
        sample = nil
        sampleDate = nil
    }

    private func clearConnection() {
        legacyStatus = .unknown
        diagnosticLineBuffer = ""
        capabilities = []
        adapters = []
        devices = []
        radio = RadioStatus(state: .stopped, transport: nil)
        pairing = PairingStatus(state: .idle, address: nil, message: nil)
        clearSample()
    }

    private func appendLog(_ text: String) {
        // Keep diagnostics bounded even when Bumble produces continuous packet logs.
        diagnostics = String(decoding: (diagnostics + text).utf8.suffix(32_768), as: UTF8.self)
    }

    func receiveDiagnostic(_ data: Data) {
        let text = String(decoding: data, as: UTF8.self)
        appendLog(text)
        // Legacy stdout contains only balance changes. Until Python emits structured
        // connection events, its explicit initialisation log is the only connection
        // signal. Buffer complete lines: pipe reads can split or combine messages.
        // Silence cannot prove disconnection; do not infer it from an unmoving CoB.
        guard phase == .starting || phase == .legacy else { return }
        diagnosticLineBuffer += text
        while let newline = diagnosticLineBuffer.firstIndex(of: "\n") {
            let line = String(diagnosticLineBuffer[..<newline]).trimmingCharacters(in: .whitespacesAndNewlines)
            diagnosticLineBuffer.removeSubrange(...newline)
            if line == "[connect] WiiBoard" {
                legacyStatus = .connected
                phase = .legacy
                startupTimeout?.cancel()
            } else if line == "timeout waiting for hci reset, retrying" {
                legacyStatus = .retrying
                phase = .legacy
                startupTimeout?.cancel()
                clearSample()
            }
        }
        diagnosticLineBuffer = String(diagnosticLineBuffer.suffix(32_768))
    }

    func clearDiagnostics() { diagnostics = "" }
}
