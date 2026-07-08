import Foundation

final class DesktopLog: @unchecked Sendable {
    let url: URL

    private let lock = NSLock()

    init(logsDirectory: URL) throws {
        try FileManager.default.createDirectory(
            at: logsDirectory,
            withIntermediateDirectories: true
        )
        self.url = logsDirectory.appendingPathComponent("desktop.log")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        write("Desktop log initialized at \(url.path)")
    }

    static func defaultLog() throws -> DesktopLog {
        let logsDirectory = FileManager.default.urls(
            for: .libraryDirectory,
            in: .userDomainMask
        )[0].appendingPathComponent("Logs/Orcheo", isDirectory: true)
        return try DesktopLog(logsDirectory: logsDirectory)
    }

    func write(_ message: String) {
        let line = "[\(Self.timestamp())] \(message)\n"
        guard let data = line.data(using: .utf8) else { return }

        lock.lock()
        defer { lock.unlock() }

        FileManager.default.createFile(atPath: url.path, contents: nil)
        guard let handle = try? FileHandle(forWritingTo: url) else { return }
        defer { try? handle.close() }
        do {
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
        } catch {
            // Logging must never become the reason the app fails to start.
        }
    }

    private static func timestamp() -> String {
        ISO8601DateFormatter().string(from: Date())
    }
}
