import AppKit
import WebKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private let supervisor = ServiceSupervisor()
    private var window: NSWindow?
    private var webView: WKWebView?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        buildWindow()
        loadStatusPage(title: "Starting Orcheo", detail: "")

        Task {
            await startServices()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task {
            await supervisor.stop()
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    @objc private func restartServices() {
        loadStatusPage(title: "Restarting Orcheo", detail: "")
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
        let logsDirectory = supervisor.logsDirectory
            ?? FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("Logs/com.orcheo.desktop", isDirectory: true)
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
        configuration.userContentController.add(self, name: "orcheoDesktopLog")
        configuration.userContentController.addUserScript(
            WKUserScript(
                source: Self.webDiagnosticsScript,
                injectionTime: .atDocumentStart,
                forMainFrameOnly: false
            )
        )

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

    func webView(
        _ webView: WKWebView,
        didFail navigation: WKNavigation!,
        withError error: Error
    ) {
        supervisor.recordDiagnostic("WebView navigation failed: \(error.localizedDescription)")
    }

    func webView(
        _ webView: WKWebView,
        didFailProvisionalNavigation navigation: WKNavigation!,
        withError error: Error
    ) {
        supervisor.recordDiagnostic(
            "WebView provisional navigation failed: \(error.localizedDescription)"
        )
    }

    // Restricts top-level navigation to the local backend origin. Studio may
    // render untrusted workflow content (e.g. under
    // ORCHEO_WORKFLOW_TRUST_MODE=allow_client_uploads), so a link or script
    // must not be able to navigate this window to an arbitrary external site.
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.allow)
            return
        }

        // The startup/error status pages are loaded via loadHTMLString(_:baseURL:),
        // which WebKit reports as an "about:" scheme navigation.
        if url.scheme == "about" {
            decisionHandler(.allow)
            return
        }

        guard let backendURL = supervisor.backendURL, isSameOrigin(url, backendURL) else {
            supervisor.recordDiagnostic("Blocked navigation to disallowed origin: \(url.absoluteString)")
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    private func isSameOrigin(_ lhs: URL, _ rhs: URL) -> Bool {
        lhs.scheme == rhs.scheme && lhs.host == rhs.host && lhs.port == rhs.port
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        supervisor.recordDiagnostic("WebView content process terminated")
        webView.reload()
    }

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard message.name == "orcheoDesktopLog" else { return }
        if let body = message.body as? [String: Any] {
            let level = body["level"] as? String ?? "log"
            let text = body["message"] as? String ?? String(describing: body)
            supervisor.recordDiagnostic("Studio \(level): \(text)")
        } else {
            supervisor.recordDiagnostic("Studio log: \(String(describing: message.body))")
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

        mainMenu.addItem(editMenuItem())

        let serviceMenuItem = NSMenuItem()
        let serviceMenu = NSMenu(title: "Services")
        serviceMenu.addItem(targetedItem(title: "Reload Studio", action: #selector(reloadStudio), keyEquivalent: "r"))
        serviceMenu.addItem(targetedItem(title: "Restart Local Services", action: #selector(restartServices), keyEquivalent: ""))
        serviceMenu.addItem(targetedItem(title: "Open Logs", action: #selector(openLogs), keyEquivalent: "l"))
        serviceMenuItem.submenu = serviceMenu
        mainMenu.addItem(serviceMenuItem)

        NSApp.mainMenu = mainMenu
    }

    private func editMenuItem() -> NSMenuItem {
        let editMenuItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")

        editMenu.addItem(responderItem(title: "Undo", action: NSSelectorFromString("undo:"), keyEquivalent: "z"))
        let redo = responderItem(title: "Redo", action: NSSelectorFromString("redo:"), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = NSEvent.ModifierFlags([.command, .shift])
        editMenu.addItem(redo)
        editMenu.addItem(.separator())
        editMenu.addItem(responderItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(responderItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(responderItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        let pasteAndMatchStyle = responderItem(
            title: "Paste and Match Style",
            action: NSSelectorFromString("pasteAsPlainText:"),
            keyEquivalent: "v"
        )
        pasteAndMatchStyle.keyEquivalentModifierMask = NSEvent.ModifierFlags([.command, .option, .shift])
        editMenu.addItem(pasteAndMatchStyle)
        editMenu.addItem(responderItem(title: "Delete", action: #selector(NSText.delete(_:)), keyEquivalent: ""))
        editMenu.addItem(.separator())
        editMenu.addItem(responderItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))

        editMenuItem.submenu = editMenu
        return editMenuItem
    }

    private func targetedItem(title: String, action: Selector, keyEquivalent: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
        item.target = self
        return item
    }

    private func responderItem(title: String, action: Selector, keyEquivalent: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
        item.target = nil
        return item
    }

    private func showError(_ error: Error) {
        let message = error.localizedDescription
        supervisor.recordDiagnostic("Presented startup error: \(message)")
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

    private static let webDiagnosticsScript = """
    (() => {
      if (window.__orcheoDesktopLogInstalled) return;
      window.__orcheoDesktopLogInstalled = true;
      const stringify = (value) => {
        try {
          if (value instanceof Error) return value.stack || value.message;
          if (typeof value === "string") return value;
          return JSON.stringify(value);
        } catch (_) {
          return String(value);
        }
      };
      const send = (level, values) => {
        try {
          window.webkit.messageHandlers.orcheoDesktopLog.postMessage({
            level,
            message: Array.from(values).map(stringify).join(" ")
          });
        } catch (_) {}
      };
      for (const level of ["error", "warn"]) {
        const original = console[level];
        console[level] = function(...args) {
          send(level, args);
          return original.apply(this, args);
        };
      }
      window.addEventListener("error", (event) => {
        send("error", [
          event.message,
          `${event.filename || "unknown"}:${event.lineno || 0}:${event.colno || 0}`
        ]);
      });
      window.addEventListener("unhandledrejection", (event) => {
        send("error", ["Unhandled promise rejection", event.reason]);
      });
    })();
    """
}
