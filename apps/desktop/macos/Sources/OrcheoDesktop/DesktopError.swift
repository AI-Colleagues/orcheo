import Foundation

enum DesktopError: LocalizedError {
    case configuration(String)
    case serviceFailed(String)

    var errorDescription: String? {
        switch self {
        case .configuration(let message):
            return message
        case .serviceFailed(let message):
            return message
        }
    }
}
