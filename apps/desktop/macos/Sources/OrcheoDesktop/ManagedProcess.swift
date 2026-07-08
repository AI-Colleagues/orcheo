import Foundation

final class ManagedProcess: @unchecked Sendable {
    let name: String
    let command: String
    let logURL: URL

    private let process = Process()
    private let logLock = NSLock()
    private var logHandle: FileHandle?
    private let desktopLog: DesktopLog?

    init(name: String, command: String, logURL: URL, desktopLog: DesktopLog? = nil) {
        self.name = name
        self.command = command
        self.logURL = logURL
        self.desktopLog = desktopLog
    }

    var isRunning: Bool {
        process.isRunning
    }

    func start(
        workingDirectory: URL,
        environment: [String: String]
    ) throws {
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        logHandle = try FileHandle(forWritingTo: logURL)
        logHandle?.seekToEndOfFile()
        writeLogLine("Starting \(name)")
        writeLogLine("Command: \(command)")
        writeLogLine("Working directory: \(workingDirectory.path)")
        desktopLog?.write("Starting process \(name): \(command)")

        let pipe = Pipe()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.writeProcessData(data)
        }

        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-lc", command]
        process.currentDirectoryURL = workingDirectory
        process.environment = environment
        process.standardOutput = pipe
        process.standardError = pipe
        process.terminationHandler = { [weak self] _ in
            self?.writeLogLine(
                "Process exited with status \(self?.process.terminationStatus ?? -1)"
            )
            self?.desktopLog?.write(
                "Process \(self?.name ?? "unknown") exited with status \(self?.process.terminationStatus ?? -1)"
            )
            pipe.fileHandleForReading.readabilityHandler = nil
            self?.closeLogHandle()
        }

        do {
            try process.run()
        } catch {
            writeLogLine("Failed to launch \(name): \(error.localizedDescription)")
            desktopLog?.write(
                "Failed to launch process \(name): \(error.localizedDescription)"
            )
            pipe.fileHandleForReading.readabilityHandler = nil
            closeLogHandle()
            throw error
        }
    }

    func stop() {
        guard process.isRunning else { return }
        writeLogLine("Stopping \(name)")
        desktopLog?.write("Stopping process \(name)")
        process.terminate()

        DispatchQueue.global(qos: .utility).async { [process] in
            Thread.sleep(forTimeInterval: 2)
            if process.isRunning {
                process.interrupt()
            }
        }
    }

    private func writeProcessData(_ data: Data) {
        logLock.lock()
        defer { logLock.unlock() }
        do {
            try logHandle?.write(contentsOf: data)
        } catch {
            desktopLog?.write("Failed writing \(name) process output: \(error.localizedDescription)")
        }
    }

    private func writeLogLine(_ message: String) {
        let line = "[\(ISO8601DateFormatter().string(from: Date()))] \(message)\n"
        guard let data = line.data(using: .utf8) else { return }
        writeProcessData(data)
    }

    private func closeLogHandle() {
        logLock.lock()
        defer { logLock.unlock() }
        try? logHandle?.close()
        logHandle = nil
    }
}
