import Foundation

enum ChatKitSettings {
    private static let signingKeyFilename = "desktop-chatkit-signing-key"

    static func signingKey(in appSupportDirectory: URL) -> String? {
        guard let value = try? String(
            contentsOf: signingKeyURL(in: appSupportDirectory),
            encoding: .utf8
        ) else {
            return nil
        }
        return normalized(value)
    }

    static func saveSigningKey(_ value: String, in appSupportDirectory: URL) throws {
        guard let key = normalized(value) else {
            throw DesktopError.configuration("ChatKit session token signing key cannot be empty.")
        }
        let url = signingKeyURL(in: appSupportDirectory)
        try key.write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: url.path
        )
    }

    static func removeSigningKey(in appSupportDirectory: URL) throws {
        let url = signingKeyURL(in: appSupportDirectory)
        guard FileManager.default.fileExists(atPath: url.path) else {
            return
        }
        try FileManager.default.removeItem(at: url)
    }

    private static func signingKeyURL(in appSupportDirectory: URL) -> URL {
        appSupportDirectory.appendingPathComponent(signingKeyFilename)
    }

    private static func normalized(_ value: String) -> String? {
        let candidate = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return candidate.isEmpty ? nil : candidate
    }
}
