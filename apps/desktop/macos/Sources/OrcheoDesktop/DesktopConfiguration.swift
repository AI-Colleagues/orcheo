import Foundation
import Darwin

struct DesktopConfiguration {
    let repoRoot: URL
    let appSupportDirectory: URL
    let logsDirectory: URL
    let studioDistDirectory: URL
    let playwrightBrowsersDirectory: URL
    let redisBinDirectory: URL?
    let backendPort: Int
    let backendURL: URL
    let backendCommand: String
    let workerCommand: String
    let beatCommand: String
    let startWorker: Bool
    let startBeat: Bool

    static func load() throws -> DesktopConfiguration {
        let environment = ProcessInfo.processInfo.environment
        let fileManager = FileManager.default
        let appSupportDirectory = try fileManager.ensureDirectory(
            base: .applicationSupportDirectory,
            component: "com.orcheo.desktop"
        )
        let logsDirectory = try fileManager.ensureDirectory(
            base: .libraryDirectory,
            component: "Logs/com.orcheo.desktop"
        )

        let repoRoot = try resolveRepoRoot(environment: environment)
        let port = try environment["ORCHEO_DESKTOP_BACKEND_PORT"].flatMap(Int.init)
            ?? findAvailableLoopbackPort()
        let backendURL = URL(string: "http://127.0.0.1:\(port)")!
        let studioDistDirectory = resolveStudioDistDirectory(
            environment: environment,
            repoRoot: repoRoot
        )
        let playwrightBrowsersDirectory = resolvePlaywrightBrowsersDirectory(
            environment: environment,
            appSupportDirectory: appSupportDirectory
        )
        let redisBinDirectory = resolveRedisBinDirectory(
            environment: environment,
            repoRoot: repoRoot
        )

        let backendCommand = environment["ORCHEO_DESKTOP_BACKEND_COMMAND"]
            ?? defaultBackendCommand(repoRoot: repoRoot, port: port)
        let workerCommand = environment["ORCHEO_DESKTOP_WORKER_COMMAND"]
            ?? "uv run celery -A orcheo_backend.worker.celery_app worker --loglevel=info"
        let beatSchedule = appSupportDirectory
            .appendingPathComponent("celerybeat-schedule")
            .path
        let beatCommand = environment["ORCHEO_DESKTOP_BEAT_COMMAND"]
            ?? "ORCHEO_CELERY_BEAT_SCHEDULE_FILE='\(shellSingleQuoteEscaped(beatSchedule))' uv run celery -A orcheo_backend.worker.celery_app beat --loglevel=info"

        return DesktopConfiguration(
            repoRoot: repoRoot,
            appSupportDirectory: appSupportDirectory,
            logsDirectory: logsDirectory,
            studioDistDirectory: studioDistDirectory,
            playwrightBrowsersDirectory: playwrightBrowsersDirectory,
            redisBinDirectory: redisBinDirectory,
            backendPort: port,
            backendURL: backendURL,
            backendCommand: backendCommand,
            workerCommand: workerCommand,
            beatCommand: beatCommand,
            startWorker: environment.boolValue(
                "ORCHEO_DESKTOP_START_WORKER",
                default: redisBinDirectory != nil
            ),
            startBeat: environment.boolValue(
                "ORCHEO_DESKTOP_START_BEAT",
                default: redisBinDirectory != nil
            )
        )
    }

    private static func resolveRepoRoot(environment: [String: String]) throws -> URL {
        if let configured = environment["ORCHEO_DESKTOP_REPO_ROOT"], !configured.isEmpty {
            return URL(fileURLWithPath: configured).standardizedFileURL
        }

        if let resourceURL = Bundle.main.resourceURL {
            let bundledRepo = resourceURL.appendingPathComponent("orcheo")
            if FileManager.default.fileExists(atPath: bundledRepo.appendingPathComponent("pyproject.toml").path) {
                return bundledRepo.standardizedFileURL
            }
        }

        var candidate = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .standardizedFileURL
        while candidate.path != "/" {
            let pyproject = candidate.appendingPathComponent("pyproject.toml")
            let backend = candidate.appendingPathComponent("apps/backend")
            if FileManager.default.fileExists(atPath: pyproject.path)
                && FileManager.default.fileExists(atPath: backend.path) {
                return candidate
            }
            candidate.deleteLastPathComponent()
        }

        throw DesktopError.configuration(
            "Set ORCHEO_DESKTOP_REPO_ROOT to an Orcheo checkout or bundle the repo under Contents/Resources/orcheo."
        )
    }

    private static func defaultBackendCommand(repoRoot: URL, port: Int) -> String {
        if FileManager.default.fileExists(
            atPath: repoRoot.appendingPathComponent("apps/backend/src").path
        ) {
            return "uv run uvicorn --app-dir apps/backend/src orcheo_backend.app:app --host 127.0.0.1 --port \(port)"
        }
        return "uv run uvicorn orcheo_backend.app:app --host 127.0.0.1 --port \(port)"
    }

    private static func resolveStudioDistDirectory(
        environment: [String: String],
        repoRoot: URL
    ) -> URL {
        if let configured = environment["ORCHEO_STUDIO_DIST_DIR"]
            ?? environment["ORCHEO_DESKTOP_STUDIO_DIST_DIR"] {
            return URL(fileURLWithPath: configured)
        }

        if let resourceURL = Bundle.main.resourceURL {
            let bundledStudio = resourceURL.appendingPathComponent("studio")
            if FileManager.default.fileExists(atPath: bundledStudio.appendingPathComponent("index.html").path) {
                return bundledStudio
            }
        }

        return repoRoot.appendingPathComponent("apps/studio/dist")
    }

    // The bundled Redis is what lets the desktop app run the Celery worker and
    // beat. Without it (a build with ORCHEO_MACOS_BUNDLE_REDIS=false, or a
    // `swift run` from a checkout) the shell falls back to in-process cron
    // dispatch and execution.
    private static func resolveRedisBinDirectory(
        environment: [String: String],
        repoRoot: URL
    ) -> URL? {
        if let configured = environment["ORCHEO_DESKTOP_REDIS_BIN_DIR"],
           !configured.isEmpty {
            let candidate = URL(fileURLWithPath: configured)
            return hasRedisServer(candidate) ? candidate : nil
        }

        if let resourceURL = Bundle.main.resourceURL {
            let bundledRedis = resourceURL
                .appendingPathComponent("redis")
                .appendingPathComponent("bin")
            if hasRedisServer(bundledRedis) {
                return bundledRedis
            }
        }

        let stagedRedis = repoRoot
            .appendingPathComponent("apps/desktop/macos/bundle/redis/bin")
        if hasRedisServer(stagedRedis) {
            return stagedRedis
        }

        return nil
    }

    private static func hasRedisServer(_ directory: URL) -> Bool {
        FileManager.default.isExecutableFile(
            atPath: directory.appendingPathComponent("redis-server").path
        )
    }

    private static func resolvePlaywrightBrowsersDirectory(
        environment: [String: String],
        appSupportDirectory: URL
    ) -> URL {
        if let configured = environment["PLAYWRIGHT_BROWSERS_PATH"],
           !configured.isEmpty {
            return URL(fileURLWithPath: configured)
        }

        if let resourceURL = Bundle.main.resourceURL {
            let bundledBrowsers = resourceURL.appendingPathComponent("ms-playwright")
            if FileManager.default.fileExists(atPath: bundledBrowsers.path) {
                return bundledBrowsers
            }
        }

        return appSupportDirectory.appendingPathComponent("ms-playwright")
    }
}

// POSIX single-quoting: close the quote, emit an escaped literal quote, then
// reopen the quote, so an embedded `'` cannot break out of the quoted value.
private func shellSingleQuoteEscaped(_ value: String) -> String {
    value.replacingOccurrences(of: "'", with: "'\\''")
}

extension FileManager {
    func ensureDirectory(base: SearchPathDirectory, component: String) throws -> URL {
        let root = urls(for: base, in: .userDomainMask)[0]
        let url = root.appendingPathComponent(component, isDirectory: true)
        try createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}

private func findAvailableLoopbackPort() throws -> Int {
    let preferredRange = 22025...22999
    for port in preferredRange {
        if isLoopbackPortAvailable(port) {
            return port
        }
    }
    throw DesktopError.configuration(
        "Could not find an available local backend port in \(preferredRange.lowerBound)-\(preferredRange.upperBound)."
    )
}

private func isLoopbackPortAvailable(_ port: Int) -> Bool {
    let socketDescriptor = socket(AF_INET, SOCK_STREAM, 0)
    if socketDescriptor < 0 {
        return false
    }
    defer {
        close(socketDescriptor)
    }

    var reuse = 1
    setsockopt(
        socketDescriptor,
        SOL_SOCKET,
        SO_REUSEADDR,
        &reuse,
        socklen_t(MemoryLayout<Int>.size)
    )

    var address = sockaddr_in()
    address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    address.sin_family = sa_family_t(AF_INET)
    address.sin_port = UInt16(port).bigEndian
    address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))

    return withUnsafePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
            bind(
                socketDescriptor,
                sockaddrPointer,
                socklen_t(MemoryLayout<sockaddr_in>.size)
            ) == 0
        }
    }
}

extension Dictionary where Key == String, Value == String {
    func boolValue(_ key: String, default defaultValue: Bool = false) -> Bool {
        guard let raw = self[key]?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              !raw.isEmpty else {
            return defaultValue
        }
        if ["1", "true", "yes", "on"].contains(raw) {
            return true
        }
        if ["0", "false", "no", "off"].contains(raw) {
            return false
        }
        return defaultValue
    }
}
