import Foundation

final class ManagedProcess: @unchecked Sendable {
    let name: String
    let command: String
    let logURL: URL

    private let process = Process()
    private var logHandle: FileHandle?

    init(name: String, command: String, logURL: URL) {
        self.name = name
        self.command = command
        self.logURL = logURL
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

        let pipe = Pipe()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.logHandle?.write(data)
        }

        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-lc", command]
        process.currentDirectoryURL = workingDirectory
        process.environment = environment
        process.standardOutput = pipe
        process.standardError = pipe
        process.terminationHandler = { [weak self] _ in
            pipe.fileHandleForReading.readabilityHandler = nil
            try? self?.logHandle?.close()
            self?.logHandle = nil
        }

        try process.run()
    }

    func stop() {
        guard process.isRunning else { return }
        process.terminate()

        DispatchQueue.global(qos: .utility).async { [process] in
            Thread.sleep(forTimeInterval: 2)
            if process.isRunning {
                process.interrupt()
            }
        }
    }
}
