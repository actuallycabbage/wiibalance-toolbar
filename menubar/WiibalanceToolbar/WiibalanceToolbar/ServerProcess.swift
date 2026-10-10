import Foundation
import Darwin

@MainActor
final class ServerProcess {
    private let process = Process()
    private let input = Pipe()
    private var stopping = false
    private var forceStop: DispatchWorkItem?

    var isRunning: Bool { process.isRunning }

    func start(
        executable: URL, arguments: [String], directory: URL,
        output: @escaping @MainActor (Data) -> Void,
        diagnostic: @escaping @MainActor (Data) -> Void,
        failed: @escaping @MainActor (String) -> Void,
        exited: @escaping @MainActor (Int32) -> Void
    ) throws {
        let stdout = Pipe()
        let stderr = Pipe()
        let finished = DispatchGroup()
        process.executableURL = executable
        process.arguments = arguments
        process.currentDirectoryURL = directory
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
        process.standardInput = input
        process.standardOutput = stdout
        process.standardError = stderr

        // A helper may exit between isRunning and write. Turn EPIPE into a Swift
        // error rather than allowing SIGPIPE to terminate the menu-bar app.
        guard fcntl(input.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }

        // Drain both pipes before reporting exit so a final error or response is not
        // discarded when a short-lived helper exits before the main queue catches up.
        finished.enter()
        process.terminationHandler = { _ in finished.leave() }
        try process.run()
        read(stdout.fileHandleForReading, finished: finished, receive: output, failed: failed)
        read(stderr.fileHandleForReading, finished: finished, receive: diagnostic, failed: failed)
        finished.notify(queue: .main) { [self] in
            forceStop?.cancel()
            forceStop = nil
            try? input.fileHandleForWriting.close()
            exited(process.terminationStatus)
        }
    }

    private func read(
        _ handle: FileHandle, finished: DispatchGroup,
        receive: @escaping @MainActor (Data) -> Void,
        failed: @escaping @MainActor (String) -> Void
    ) {
        finished.enter()
        DispatchQueue(label: "WiiBalance.pipe-reader").async {
            defer {
                try? handle.close()
                finished.leave()
            }
            var bytes = [UInt8](repeating: 0, count: 16_384)
            while true {
                // FileHandle.read(upToCount:) can wait to fill the requested length.
                // A pipe may be silent indefinitely after a tiny ready/response event;
                // one POSIX read delivers those bytes immediately without waiting for EOF.
                let count = Darwin.read(handle.fileDescriptor, &bytes, bytes.count)
                if count == 0 { return }
                if count < 0 {
                    if errno == EINTR { continue }
                    let message = String(cString: strerror(errno))
                    DispatchQueue.main.async { failed("Could not read the Python pipe: \(message)") }
                    return
                }
                let data = Data(bytes.prefix(count))
                // Back-pressure bounds queued data as well as the decoder/log buffer.
                // The main actor never waits on these readers during shutdown.
                DispatchQueue.main.sync { receive(data) }
            }
        }
    }

    func send(_ command: ServerCommand) throws {
        guard process.isRunning, !stopping else { throw WireError.invalid("The Python server is not running.") }
        try input.fileHandleForWriting.write(contentsOf: command.encoded())
    }

    func stop() {
        guard process.isRunning, !stopping else { return }
        stopping = true
        // Launch Python directly: terminating a uv wrapper can orphan the USB owner.
        // SIGTERM lets the server release Bluetooth; bound shutdown if initialisation hangs.
        process.terminate()
        let work = DispatchWorkItem { [self] in
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        }
        forceStop = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: work)
    }
}
