import AppKit
import WebKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private let supervisor = ServiceSupervisor()
    private var window: NSWindow?
    private var webView: WKWebView?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        buildWindow()
        loadStatusPage(title: "Starting Orcheo", detail: "Launching local services...")

        Task {
            await startServices()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        supervisor.stop()
        return .terminateNow
    }

    @objc private func restartServices() {
        loadStatusPage(title: "Restarting Orcheo", detail: "Stopping and launching local services...")
        Task {
            do {
                let url = try await supervisor.restart()
                webView?.load(URLRequest(url: url))
            } catch {
                showError(error)
            }
        }
    }

    @objc private func openLogs() {
        guard let logsDirectory = supervisor.logsDirectory else { return }
        NSWorkspace.shared.open(logsDirectory)
    }

    @objc private func checkForUpdates() {
        let message: String
        if let appcastURL = supervisor.configuration?.appcastURL {
            message = "Update feed configured:\n\(appcastURL.absoluteString)\n\nWire Sparkle here when the release appcast is available."
        } else {
            message = "Set ORCHEO_SPARKLE_FEED_URL and connect Sparkle before shipping signed releases."
        }
        let alert = NSAlert()
        alert.messageText = "Check for Updates"
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc private func reloadStudio() {
        webView?.reload(nil)
    }

    private func startServices() async {
        do {
            let url = try await supervisor.start()
            webView?.load(URLRequest(url: url))
        } catch {
            showError(error)
        }
    }

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        self.webView = webView

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.center()
        window.title = "Orcheo"
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        self.window = window
        NSApp.activate(ignoringOtherApps: true)
    }

    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping @MainActor @Sendable ([URL]?) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canCreateDirectories = false
        panel.resolvesAliases = true

        if let window = webView.window {
            panel.beginSheetModal(for: window) { response in
                completionHandler(response == .OK ? panel.urls : nil)
            }
        } else {
            completionHandler(panel.runModal() == .OK ? panel.urls : nil)
        }
    }

    private func buildMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(targetedItem(title: "Check for Updates...", action: #selector(checkForUpdates), keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Quit Orcheo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        let serviceMenuItem = NSMenuItem()
        let serviceMenu = NSMenu(title: "Services")
        serviceMenu.addItem(targetedItem(title: "Reload Studio", action: #selector(reloadStudio), keyEquivalent: "r"))
        serviceMenu.addItem(targetedItem(title: "Restart Local Services", action: #selector(restartServices), keyEquivalent: ""))
        serviceMenu.addItem(targetedItem(title: "Open Logs", action: #selector(openLogs), keyEquivalent: "l"))
        serviceMenuItem.submenu = serviceMenu
        mainMenu.addItem(serviceMenuItem)

        NSApp.mainMenu = mainMenu
    }

    private func targetedItem(title: String, action: Selector, keyEquivalent: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
        item.target = self
        return item
    }

    private func showError(_ error: Error) {
        let message = error.localizedDescription
        loadStatusPage(title: "Orcheo could not start", detail: message)

        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Orcheo could not start"
        alert.informativeText = message
        alert.addButton(withTitle: "Open Logs")
        alert.addButton(withTitle: "OK")
        if alert.runModal() == .alertFirstButtonReturn {
            openLogs()
        }
    }

    private func loadStatusPage(title: String, detail: String) {
        let html = """
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <style>
              html, body { height: 100%; margin: 0; }
              body {
                align-items: center;
                background: #101114;
                color: #f5f5f5;
                display: flex;
                font: 14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                justify-content: center;
              }
              main { max-width: 560px; padding: 32px; text-align: center; }
              h1 { font-size: 24px; font-weight: 650; margin: 0 0 12px; }
              p { color: #b7bbc3; line-height: 1.5; margin: 0; white-space: pre-wrap; }
            </style>
          </head>
          <body>
            <main>
              <h1>\(escapeHTML(title))</h1>
              <p>\(escapeHTML(detail))</p>
            </main>
          </body>
        </html>
        """
        webView?.loadHTMLString(html, baseURL: nil)
    }

    private func escapeHTML(_ value: String) -> String {
        value
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\"", with: "&quot;")
    }
}
