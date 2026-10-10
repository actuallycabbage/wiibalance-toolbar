import AppKit

enum AppSettings {
    static var projectRoot: String {
        get {
            if let saved = UserDefaults.standard.string(forKey: "projectRoot") { return saved }
            // Development builds can find their checkout. A moved/distributed app asks
            // for a folder instead of silently launching from the original developer's path.
            let sourceRoot = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent().deletingLastPathComponent()
                .deletingLastPathComponent().deletingLastPathComponent().path
            return FileManager.default.fileExists(atPath: sourceRoot + "/app/server/server.py") ? sourceRoot : ""
        }
        set { UserDefaults.standard.set(newValue, forKey: "projectRoot") }
    }

    static var pythonPath: String {
        get { UserDefaults.standard.string(forKey: "pythonPath") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "pythonPath") }
    }

    static func launchConfiguration() throws -> (root: URL, python: URL) {
        let root = URL(fileURLWithPath: (projectRoot as NSString).expandingTildeInPath, isDirectory: true)
        guard !projectRoot.isEmpty,
              FileManager.default.fileExists(atPath: root.appendingPathComponent("app/server/server.py").path) else {
            throw WireError.invalid("Choose your WiiBalance project folder in Settings → Advanced.")
        }
        let python = pythonPath.isEmpty
            ? root.appendingPathComponent(".venv/bin/python")
            : URL(fileURLWithPath: (pythonPath as NSString).expandingTildeInPath)
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw WireError.invalid("The connection service needs setting up. Check Settings → Advanced.")
        }
        return (root, python)
    }
}
