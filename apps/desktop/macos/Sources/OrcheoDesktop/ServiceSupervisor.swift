import Foundation

@MainActor
final class ServiceSupervisor {
    private(set) var configuration: DesktopConfiguration?
    private var processes: [ManagedProcess] = []
    private var managedDesktopPostgres = false

    var backendURL: URL? {
        configuration?.backendURL
    }

    var logsDirectory: URL? {
        configuration?.logsDirectory
    }

    func start() async throws -> URL {
        let configuration = try DesktopConfiguration.load()
        self.configuration = configuration
        try FileManager.default.createDirectory(
            at: configuration.logsDirectory,
            withIntermediateDirectories: true
        )

        let environment = try processEnvironment(configuration: configuration)
        try startProcess(
            name: "backend",
            command: configuration.backendCommand,
            configuration: configuration,
            environment: environment
        )

        if configuration.startWorker {
            try startProcess(
                name: "worker",
                command: configuration.workerCommand,
                configuration: configuration,
                environment: environment
            )
        }

        if configuration.startBeat {
            try startProcess(
                name: "beat",
                command: configuration.beatCommand,
                configuration: configuration,
                environment: environment
            )
        }

        try await waitForBackend(configuration.backendURL)
        return configuration.backendURL
    }

    func restart() async throws -> URL {
        stop()
        return try await start()
    }

    func stop() {
        for process in processes.reversed() {
            process.stop()
        }
        processes.removeAll()
        stopManagedDesktopPostgres()
    }

    private func startProcess(
        name: String,
        command: String,
        configuration: DesktopConfiguration,
        environment: [String: String]
    ) throws {
        let process = ManagedProcess(
            name: name,
            command: command,
            logURL: configuration.logsDirectory.appendingPathComponent("\(name).log")
        )
        try process.start(
            workingDirectory: configuration.repoRoot,
            environment: environment
        )
        processes.append(process)
    }

    private func processEnvironment(configuration: DesktopConfiguration) throws -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        applyDesktopEnvFile(
            to: &environment,
            configuration: configuration
        )
        environment["ORCHEO_HOST"] = "127.0.0.1"
        environment["ORCHEO_PORT"] = String(configuration.backendPort)
        environment["ORCHEO_STUDIO_URL"] = configuration.backendURL.absoluteString
        environment["ORCHEO_STUDIO_DIST_DIR"] = configuration.studioDistDirectory.path
        environment["ORCHEO_AUTH_MODE"] = environment["ORCHEO_AUTH_MODE"] ?? "disabled"
        environment["ORCHEO_CORS_ALLOW_ORIGINS"] = "[\"\(configuration.backendURL.absoluteString)\"]"
        configureDesktopWorkflowUploadPolicy(environment: &environment)
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
        return environment
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
        let scriptURL = configuration.repoRoot
            .appendingPathComponent("scripts/desktop-postgres.sh")
        if !FileManager.default.isExecutableFile(atPath: scriptURL.path) {
            throw DesktopError.configuration(
                "Desktop Postgres helper is missing or not executable at \(scriptURL.path)."
            )
        }

        let process = Process()
        let outputPipe = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [
            scriptURL.path,
            action,
            configuration.appSupportDirectory.path,
            configuration.logsDirectory.path,
        ]
        process.currentDirectoryURL = configuration.repoRoot
        process.environment = ProcessInfo.processInfo.environment
        process.standardOutput = outputPipe
        process.standardError = outputPipe

        try process.run()
        process.waitUntilExit()

        let output = String(
            data: outputPipe.fileHandleForReading.readDataToEndOfFile(),
            encoding: .utf8
        )?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

        if process.terminationStatus != 0 {
            throw DesktopError.configuration(output.isEmpty
                ? "Desktop Postgres helper failed."
                : output)
        }
        return output
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

    private func waitForBackend(_ baseURL: URL) async throws {
        let healthURL = baseURL.appendingPathComponent("api/system/health")
        let deadline = Date().addingTimeInterval(60)
        while Date() < deadline {
            do {
                let (_, response) = try await URLSession.shared.data(from: healthURL)
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    return
                }
            } catch {
                try await Task.sleep(nanoseconds: 500_000_000)
                continue
            }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
        throw DesktopError.serviceFailed("Backend did not become healthy at \(healthURL.absoluteString).")
    }
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
