import Foundation

@MainActor
final class ServiceSupervisor {
    private(set) var configuration: DesktopConfiguration?
    private var processes: [ManagedProcess] = []
    private var managedDesktopPostgres = false
    private var managedDesktopRedis = false
    // Set once the Celery broker is confirmed reachable. Worker and beat are
    // only started when it is: without a broker they would spin uselessly, and
    // the in-process paths are the better fallback.
    private var brokerReady = false
    private var workerProcess: ManagedProcess?
    private var beatProcess: ManagedProcess?
    private var desktopLog: DesktopLog?

    var backendURL: URL? {
        configuration?.backendURL
    }

    var logsDirectory: URL? {
        configuration?.logsDirectory
    }

    func start() async throws -> URL {
        desktopLog = try? DesktopLog.defaultLog()
        desktopLog?.write("Starting Orcheo desktop services")
        do {
            let configuration = try DesktopConfiguration.load()
            self.configuration = configuration
            desktopLog = try DesktopLog(logsDirectory: configuration.logsDirectory)
            logConfiguration(configuration)
            guard FileManager.default.fileExists(
                atPath: configuration.studioDistDirectory.appendingPathComponent("index.html").path
            ) else {
                throw DesktopError.configuration(
                    "Studio is not built at \(configuration.studioDistDirectory.path). Run `bash apps/desktop/macos/scripts/build-studio.sh` first."
                )
            }
            try FileManager.default.createDirectory(
                at: configuration.logsDirectory,
                withIntermediateDirectories: true
            )

            let environment = try processEnvironment(configuration: configuration)
            desktopLog?.write(
                "Resolved process environment: \(redactedEnvironmentSummary(environment))"
            )
            try startProcess(
                name: "backend",
                command: configuration.backendCommand,
                configuration: configuration,
                environment: environment
            )

            if configuration.startWorker && brokerReady {
                workerProcess = try startProcess(
                    name: "worker",
                    command: configuration.workerCommand,
                    configuration: configuration,
                    environment: environment
                )
            }

            if configuration.startBeat && brokerReady {
                beatProcess = try startProcess(
                    name: "beat",
                    command: configuration.beatCommand,
                    configuration: configuration,
                    environment: environment
                )
            }

            try await waitForBackend(configuration.backendURL)
            desktopLog?.write("Backend is healthy at \(configuration.backendURL.absoluteString)")
            try await recoverFromDeadCeleryProcesses(
                configuration: configuration,
                environment: environment
            )
            return configuration.backendURL
        } catch {
            desktopLog?.write("Service startup failed: \(error.localizedDescription)")
            // Startup can fail after some processes (or managed Postgres) are
            // already running -- e.g. Postgres started but vault-key creation
            // failed. Stop whatever did start so a failed launch does not
            // leak processes, held ports, or app-support state.
            await stop()
            throw error
        }
    }

    func restart() async throws -> URL {
        await stop()
        return try await start()
    }

    func stop() async {
        desktopLog?.write("Stopping Orcheo desktop services")
        for process in processes.reversed() {
            await process.stop()
        }
        processes.removeAll()
        workerProcess = nil
        beatProcess = nil
        stopManagedDesktopRedis()
        stopManagedDesktopPostgres()
        brokerReady = false
    }

    func recordDiagnostic(_ message: String) {
        if desktopLog == nil {
            desktopLog = try? DesktopLog.defaultLog()
        }
        desktopLog?.write(message)
    }

    @discardableResult
    private func startProcess(
        name: String,
        command: String,
        configuration: DesktopConfiguration,
        environment: [String: String]
    ) throws -> ManagedProcess {
        let process = ManagedProcess(
            name: name,
            command: command,
            logURL: configuration.logsDirectory.appendingPathComponent("\(name).log"),
            desktopLog: desktopLog
        )
        try process.start(
            workingDirectory: configuration.repoRoot,
            environment: environment
        )
        processes.append(process)
        return process
    }

    // The worker and beat start alongside the backend, so by the time it is
    // healthy either has had time to fail (a bad command, a missing module, a
    // broker that went away). Rather than leave cron silently dead, hand the
    // work of whichever died back to the backend and restart it.
    private func recoverFromDeadCeleryProcesses(
        configuration: DesktopConfiguration,
        environment: [String: String]
    ) async throws {
        let workerDead = configuration.startWorker && brokerReady
            && !(workerProcess?.isRunning ?? false)
        let beatDead = configuration.startBeat && brokerReady
            && !(beatProcess?.isRunning ?? false)
        guard workerDead || beatDead else {
            return
        }

        if workerDead {
            desktopLog?.write(
                "Worker is not running; taking over run execution in the backend. "
                    + "See \(configuration.logsDirectory.appendingPathComponent("worker.log").path)"
            )
        }
        if beatDead {
            desktopLog?.write(
                "Beat is not running; taking over cron dispatch in the backend. "
                    + "See \(configuration.logsDirectory.appendingPathComponent("beat.log").path)"
            )
        }

        var recoveredEnvironment = environment
        if beatDead {
            recoveredEnvironment["ORCHEO_INPROCESS_CRON"] = "true"
        }
        if workerDead {
            recoveredEnvironment["ORCHEO_INPROCESS_EXECUTION"] = "true"
        }

        try await restartBackend(
            configuration: configuration,
            environment: recoveredEnvironment
        )
    }

    private func restartBackend(
        configuration: DesktopConfiguration,
        environment: [String: String]
    ) async throws {
        if let backend = processes.first {
            await backend.stop()
            processes.removeFirst()
        }
        let backend = try startProcess(
            name: "backend",
            command: configuration.backendCommand,
            configuration: configuration,
            environment: environment
        )
        // waitForBackend watches processes.first for an early exit.
        processes.removeAll { $0 === backend }
        processes.insert(backend, at: 0)
        try await waitForBackend(configuration.backendURL)
        desktopLog?.write(
            "Backend restarted with in-process fallbacks at \(configuration.backendURL.absoluteString)"
        )
    }

    private func processEnvironment(configuration: DesktopConfiguration) throws -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        applyDesktopEnvFile(
            to: &environment,
            configuration: configuration
        )
        if let signingKey = ChatKitSettings.signingKey(
            in: configuration.appSupportDirectory
        ) {
            // A key explicitly saved in the desktop settings takes precedence
            // over launch-time environment values so it works from Finder too.
            environment["ORCHEO_CHATKIT_TOKEN_SIGNING_KEY"] = signingKey
        }
        environment["ORCHEO_HOST"] = "127.0.0.1"
        environment["ORCHEO_PORT"] = String(configuration.backendPort)
        environment["ORCHEO_STUDIO_URL"] = configuration.backendURL.absoluteString
        environment["ORCHEO_STUDIO_DIST_DIR"] = configuration.studioDistDirectory.path
        environment["ORCHEO_AUTH_MODE"] = environment["ORCHEO_AUTH_MODE"] ?? "disabled"
        environment["ORCHEO_CORS_ALLOW_ORIGINS"] = "[\"\(configuration.backendURL.absoluteString)\"]"
        configureDesktopWorkflowUploadPolicy(environment: &environment)
        configureDesktopRedis(
            environment: &environment,
            configuration: configuration
        )
        configureDesktopSchedulingMode(
            environment: &environment,
            configuration: configuration
        )
        environment["ORCHEO_DESKTOP_APP_SUPPORT_DIR"] = configuration.appSupportDirectory.path
        environment["ORCHEO_DESKTOP_LOG_DIR"] = configuration.logsDirectory.path
        try configureDesktopPostgresDSN(
            environment: &environment,
            configuration: configuration
        )
        try configureDesktopVaultKey(
            environment: &environment,
            configuration: configuration
        )
        environment["UV_CACHE_DIR"] = configuration.appSupportDirectory
            .appendingPathComponent("uv-cache")
            .path
        environment["UV_PROJECT_ENVIRONMENT"] = configuration.appSupportDirectory
            .appendingPathComponent("python-env")
            .path
        environment["PLAYWRIGHT_BROWSERS_PATH"] = configuration.playwrightBrowsersDirectory.path
        configurePythonPath(environment: &environment, configuration: configuration)
        configureProcessPath(environment: &environment)
        return environment
    }

    // `orcheo-backend` is not a runtime dependency of the root project (only of
    // the `examples` group), so `uv run` never installs it into the packaged
    // environment. The backend command works around that with uvicorn's
    // `--app-dir`; celery has no equivalent, so put the same directory on
    // PYTHONPATH for every supervised process.
    private func configurePythonPath(
        environment: inout [String: String],
        configuration: DesktopConfiguration
    ) {
        let backendSource = configuration.repoRoot
            .appendingPathComponent("apps/backend/src")
        guard FileManager.default.fileExists(atPath: backendSource.path) else {
            return
        }

        var entries = [backendSource.path]
        if let existing = nonEmpty(environment["PYTHONPATH"]) {
            for entry in existing.split(separator: ":").map(String.init)
            where !entries.contains(entry) {
                entries.append(entry)
            }
        }
        environment["PYTHONPATH"] = entries.joined(separator: ":")
    }

    // Finder launches inherit a minimal PATH that usually misses `uv` (and
    // Homebrew), so extend it with the common install locations instead of
    // relying purely on the login shell profile.
    private func configureProcessPath(environment: inout [String: String]) {
        var candidates: [String] = []
        if let existing = nonEmpty(environment["PATH"]) {
            candidates.append(contentsOf: existing.split(separator: ":").map(String.init))
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        candidates.append("\(home)/.local/bin")
        candidates.append("\(home)/.cargo/bin")
        candidates.append(contentsOf: [
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ])

        var seen: [String] = []
        for candidate in candidates where !seen.contains(candidate) {
            seen.append(candidate)
        }
        environment["PATH"] = seen.joined(separator: ":")
    }

    private func logConfiguration(_ configuration: DesktopConfiguration) {
        desktopLog?.write("Repo root: \(configuration.repoRoot.path)")
        desktopLog?.write("App support: \(configuration.appSupportDirectory.path)")
        desktopLog?.write("Logs: \(configuration.logsDirectory.path)")
        desktopLog?.write("Backend URL: \(configuration.backendURL.absoluteString)")
        desktopLog?.write("Studio dist: \(configuration.studioDistDirectory.path)")
        desktopLog?.write("Playwright browsers: \(configuration.playwrightBrowsersDirectory.path)")
        desktopLog?.write("Backend command: \(configuration.backendCommand)")
        desktopLog?.write(
            "Bundled Redis: \(configuration.redisBinDirectory?.path ?? "none")"
        )
        desktopLog?.write("Worker enabled: \(configuration.startWorker)")
        desktopLog?.write("Beat enabled: \(configuration.startBeat)")
    }

    private func configureDesktopWorkflowUploadPolicy(
        environment: inout [String: String]
    ) {
        let definitionMode = nonEmpty(environment["ORCHEO_WORKFLOW_DEFINITION_MODE"])
            ?? "unrestricted"
        environment["ORCHEO_WORKFLOW_DEFINITION_MODE"] = definitionMode

        if nonEmpty(environment["ORCHEO_WORKFLOW_TRUST_MODE"]) == nil
            && definitionMode.trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased() == "unrestricted" {
            environment["ORCHEO_WORKFLOW_TRUST_MODE"] = "allow_client_uploads"
        }
    }

    // The backend dispatches cron triggers and executes runs in-process by
    // default, which is what makes unattended schedules fire without Redis.
    // When the user opted into running Beat or the worker themselves, turn the
    // matching in-process path off so nothing is dispatched or executed twice.
    private func configureDesktopSchedulingMode(
        environment: inout [String: String],
        configuration: DesktopConfiguration
    ) {
        if nonEmpty(environment["ORCHEO_INPROCESS_CRON"]) == nil {
            environment["ORCHEO_INPROCESS_CRON"] =
                (configuration.startBeat && brokerReady) ? "false" : "true"
        }
        if nonEmpty(environment["ORCHEO_INPROCESS_EXECUTION"]) == nil {
            environment["ORCHEO_INPROCESS_EXECUTION"] =
                (configuration.startWorker && brokerReady) ? "false" : "true"
        }
    }

    private func inheritedBrokerResponds(
        _ url: String,
        configuration: DesktopConfiguration
    ) -> Bool {
        do {
            _ = try runDesktopServiceScript(
                name: "redis",
                action: "ping",
                configuration: configuration,
                extraEnvironment: ["ORCHEO_DESKTOP_REDIS_PING_URL": url]
            )
            return true
        } catch {
            return false
        }
    }

    // Starts the bundled Redis so the Celery worker and beat have a broker.
    // A failure here is not fatal: the backend falls back to in-process cron
    // dispatch and execution, which needs no broker at all.
    private func configureDesktopRedis(
        environment: inout [String: String],
        configuration: DesktopConfiguration
    ) {
        guard configuration.startWorker || configuration.startBeat else {
            return
        }

        if let inheritedURL = nonEmpty(environment["REDIS_URL"]) {
            environment["REDIS_URL"] = inheritedURL
            // A non-empty REDIS_URL is a claim, not a live broker. Celery does
            // not exit when its broker is unreachable, it retries forever, so
            // trusting the string would turn the in-process fallbacks off and
            // leave schedules dead while every process still looks healthy.
            if inheritedBrokerResponds(inheritedURL, configuration: configuration) {
                desktopLog?.write("Using inherited REDIS_URL for the Celery broker")
                brokerReady = true
            } else {
                desktopLog?.write(
                    "Inherited REDIS_URL is not answering; falling back to "
                        + "in-process cron and execution"
                )
            }
            return
        }

        guard let redisBinDirectory = configuration.redisBinDirectory else {
            desktopLog?.write(
                "No bundled Redis found; falling back to in-process cron and execution"
            )
            return
        }

        do {
            let url = try runDesktopServiceScript(
                name: "redis",
                action: "start",
                configuration: configuration,
                extraEnvironment: [
                    "ORCHEO_DESKTOP_REDIS_BIN_DIR": redisBinDirectory.path
                ]
            )
            guard let brokerURL = nonEmpty(url) else {
                throw DesktopError.configuration(
                    "Desktop Redis helper produced no broker URL."
                )
            }
            environment["REDIS_URL"] = brokerURL
            managedDesktopRedis = true
            brokerReady = true
        } catch {
            desktopLog?.write(
                "Desktop Redis failed to start (\(error.localizedDescription)); "
                    + "falling back to in-process cron and execution"
            )
        }
    }

    private func applyDesktopEnvFile(
        to environment: inout [String: String],
        configuration: DesktopConfiguration
    ) {
        let values = loadDotenvValues(
            at: configuration.appSupportDirectory.appendingPathComponent("desktop.env")
        )
        for (key, value) in values where environment[key] == nil {
            environment[key] = value
        }
    }

    private func configureDesktopPostgresDSN(
        environment: inout [String: String],
        configuration: DesktopConfiguration
    ) throws {
        let dotenvValues = loadDotenvValues(
            at: configuration.repoRoot.appendingPathComponent(".env")
        )

        if let desktopDSN = nonEmpty(environment["ORCHEO_DESKTOP_POSTGRES_DSN"]) {
            environment["ORCHEO_POSTGRES_DSN"] = desktopDSN
            return
        }

        if let inheritedDSN = nonEmpty(environment["ORCHEO_POSTGRES_DSN"]) {
            if usesDeploymentPostgresPort(inheritedDSN) {
                throw DesktopError.configuration(
                    "The desktop app refuses to use ORCHEO_POSTGRES_DSN on localhost:5432 because that is the normal server deployment port. Set ORCHEO_DESKTOP_POSTGRES_DSN to a separate desktop database."
                )
            }
            return
        }

        if let dotenvDSN = nonEmpty(dotenvValues["ORCHEO_POSTGRES_DSN"]) {
            if usesDeploymentPostgresPort(dotenvDSN) {
                throw DesktopError.configuration(
                    "The bundled .env points ORCHEO_POSTGRES_DSN at localhost:5432. Rebuild without .env or set ORCHEO_DESKTOP_POSTGRES_DSN to a separate desktop database."
                )
            }
            return
        }

        let managedDSN = try runDesktopPostgresScript(
            action: "start",
            configuration: configuration
        )
        managedDesktopPostgres = true
        environment["ORCHEO_POSTGRES_DSN"] = managedDSN
    }

    private func configureDesktopVaultKey(
        environment: inout [String: String],
        configuration: DesktopConfiguration
    ) throws {
        let dotenvValues = loadDotenvValues(
            at: configuration.repoRoot.appendingPathComponent(".env")
        )
        if nonEmpty(environment["ORCHEO_VAULT_ENCRYPTION_KEY"]) != nil
            || nonEmpty(dotenvValues["ORCHEO_VAULT_ENCRYPTION_KEY"]) != nil {
            return
        }

        let keyURL = configuration.appSupportDirectory
            .appendingPathComponent("desktop-vault.key")
        if let existing = try? String(contentsOf: keyURL, encoding: .utf8),
           let key = nonEmpty(existing) {
            environment["ORCHEO_VAULT_ENCRYPTION_KEY"] = key
            return
        }

        let key = "\(UUID().uuidString)-\(UUID().uuidString)"
        try key.write(to: keyURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: keyURL.path
        )
        environment["ORCHEO_VAULT_ENCRYPTION_KEY"] = key
    }

    private func runDesktopPostgresScript(
        action: String,
        configuration: DesktopConfiguration
    ) throws -> String {
        try runDesktopServiceScript(
            name: "postgres",
            action: action,
            configuration: configuration
        )
    }

    // Shared runner for scripts/desktop-<name>.sh, which all take the same
    // <action> <app-support-dir> <log-dir> arguments and print the URL the
    // backend should connect to.
    private func runDesktopServiceScript(
        name: String,
        action: String,
        configuration: DesktopConfiguration,
        extraEnvironment: [String: String] = [:]
    ) throws -> String {
        let label = name.capitalized
        let scriptURL = configuration.repoRoot
            .appendingPathComponent("scripts/desktop-\(name).sh")
        if !FileManager.default.fileExists(atPath: scriptURL.path) {
            throw DesktopError.configuration(
                "Desktop \(label) helper is missing at \(scriptURL.path)."
            )
        }

        let process = Process()
        let outputPipe = Pipe()
        desktopLog?.write("Running desktop \(label) helper: \(action)")
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [
            scriptURL.path,
            action,
            configuration.appSupportDirectory.path,
            configuration.logsDirectory.path,
        ]
        process.currentDirectoryURL = configuration.repoRoot
        var scriptEnvironment = ProcessInfo.processInfo.environment
        for (key, value) in extraEnvironment {
            scriptEnvironment[key] = value
        }
        process.environment = scriptEnvironment
        process.standardOutput = outputPipe
        process.standardError = outputPipe

        try process.run()
        process.waitUntilExit()

        let output = String(
            data: outputPipe.fileHandleForReading.readDataToEndOfFile(),
            encoding: .utf8
        )?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        desktopLog?.write(
            "Desktop \(label) helper \(action) exited with status \(process.terminationStatus)"
        )
        if !output.isEmpty {
            desktopLog?.write("Desktop \(label) helper \(action) output: \(output)")
        }

        if process.terminationStatus != 0 {
            throw DesktopError.configuration(output.isEmpty
                ? "Desktop \(label) helper failed."
                : output)
        }
        return output
    }

    private func stopManagedDesktopRedis() {
        guard managedDesktopRedis, let configuration else {
            return
        }
        _ = try? runDesktopServiceScript(
            name: "redis",
            action: "stop",
            configuration: configuration,
            extraEnvironment: configuration.redisBinDirectory.map {
                ["ORCHEO_DESKTOP_REDIS_BIN_DIR": $0.path]
            } ?? [:]
        )
        managedDesktopRedis = false
    }

    private func stopManagedDesktopPostgres() {
        guard managedDesktopPostgres, let configuration else {
            return
        }
        _ = try? runDesktopPostgresScript(
            action: "stop",
            configuration: configuration
        )
        managedDesktopPostgres = false
    }

    // A cold first launch has to let `uv` create a virtualenv and install the
    // whole workspace (and, the first time, initialize the bundled Postgres
    // data directory), which can comfortably exceed a minute. This loop
    // already returns as soon as the backend process itself exits, so a
    // generous deadline here only guards against a truly hung process.
    private static let backendHealthTimeout: TimeInterval = 300

    private func waitForBackend(_ baseURL: URL) async throws {
        let healthURL = baseURL.appendingPathComponent("api/system/health")
        let deadline = Date().addingTimeInterval(Self.backendHealthTimeout)
        var lastError: String?
        while Date() < deadline {
            do {
                let (_, response) = try await URLSession.shared.data(from: healthURL)
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    return
                }
                lastError = "Unexpected health response: \(String(describing: (response as? HTTPURLResponse)?.statusCode))"
            } catch {
                lastError = error.localizedDescription
            }

            if let backend = processes.first, !backend.isRunning {
                var message = "Backend exited before becoming healthy at \(healthURL.absoluteString) with status \(backend.terminationStatus)."
                let logTail = readLogTail(at: backend.logURL, maxBytes: 4000)
                if !logTail.isEmpty {
                    message += "\n\nBackend log tail:\n\(logTail)"
                }
                desktopLog?.write(message)
                throw DesktopError.serviceFailed(message)
            }

            try await Task.sleep(nanoseconds: 500_000_000)
        }
        let suffix = lastError.map { " Last error: \($0)" } ?? ""
        desktopLog?.write("Backend health check timed out at \(healthURL.absoluteString).\(suffix)")
        throw DesktopError.serviceFailed("Backend did not become healthy at \(healthURL.absoluteString).\(suffix)")
    }
}

private func readLogTail(at url: URL, maxBytes: Int) -> String {
    guard let handle = try? FileHandle(forReadingFrom: url) else {
        return ""
    }
    defer { try? handle.close() }
    guard let length = try? handle.seekToEnd() else {
        return ""
    }
    let start = length > UInt64(maxBytes) ? length - UInt64(maxBytes) : 0
    guard (try? handle.seek(toOffset: start)) != nil,
          let data = try? handle.readToEnd() else {
        return ""
    }
    return String(decoding: data, as: UTF8.self)
}

private func nonEmpty(_ value: String?) -> String? {
    guard let candidate = value?.trimmingCharacters(in: .whitespacesAndNewlines),
          !candidate.isEmpty else {
        return nil
    }
    return candidate
}

private func loadDotenvValues(at url: URL) -> [String: String] {
    guard let contents = try? String(contentsOf: url, encoding: .utf8) else {
        return [:]
    }

    var values: [String: String] = [:]
    for rawLine in contents.split(whereSeparator: \.isNewline) {
        var line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
        if line.isEmpty || line.hasPrefix("#") {
            continue
        }
        if line.hasPrefix("export ") {
            line.removeFirst("export ".count)
        }
        guard let separator = line.firstIndex(of: "=") else {
            continue
        }
        let key = String(line[..<separator])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        var value = String(line[line.index(after: separator)...])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if (value.hasPrefix("\"") && value.hasSuffix("\""))
            || (value.hasPrefix("'") && value.hasSuffix("'")) {
            value.removeFirst()
            value.removeLast()
        }
        values[key] = value
    }
    return values
}

private func redactedEnvironmentSummary(_ environment: [String: String]) -> String {
    let interestingPrefixes = [
        "ORCHEO_",
        "UV_",
        "PLAYWRIGHT_",
        "PATH",
    ]
    return environment.keys
        .filter { key in interestingPrefixes.contains(where: { key.hasPrefix($0) }) }
        .sorted()
        .map { key in "\(key)=\(redactedValue(key: key, value: environment[key] ?? ""))" }
        .joined(separator: ", ")
}

private func redactedValue(key: String, value: String) -> String {
    let redactedMarkers = ["KEY", "TOKEN", "SECRET", "PASSWORD", "DSN", "DATABASE_URL"]
    if redactedMarkers.contains(where: { key.uppercased().contains($0) }) {
        return value.isEmpty ? "" : "<redacted>"
    }
    return value
}

private func usesDeploymentPostgresPort(_ dsn: String) -> Bool {
    guard let url = URL(string: dsn) else {
        return dsn.contains("localhost:5432") || dsn.contains("127.0.0.1:5432")
    }
    let host = url.host(percentEncoded: false)?.lowercased()
    guard host == "localhost" || host == "127.0.0.1" || host == "::1" else {
        return false
    }
    return url.port == nil || url.port == 5432
}
