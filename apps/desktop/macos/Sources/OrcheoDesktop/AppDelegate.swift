import AppKit
import WebKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
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

    // Closing the window hides it while the local services keep running, and
    // clicking the Dock icon brings it back -- the same lifecycle as the
    // Tauri shell's hide-on-close behavior.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        return false
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        if !flag, let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
        return true
    }

    @objc private func restartServices() {
        loadStatusPage(title: "Restarting Orcheo", detail: "Stopping and relaunching local services...")
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
        try? FileManager.default.createDirectory(
            at: logsDirectory,
            withIntermediateDirectories: true
        )
        NSWorkspace.shared.open(logsDirectory)
    }

    @objc private func reloadStudio() {
        webView?.reload(nil)
    }

    @objc private func showAbout() {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
            ?? "Development"
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Orcheo",
            .applicationVersion: version,
        ])
    }

    @objc private func openChatKitSettings() {
        guard let appSupportDirectory = chatKitSettingsDirectory() else {
            showError(DesktopError.configuration("Could not resolve the desktop settings directory."))
            return
        }

        let alert = NSAlert()
        alert.messageText = "ChatKit Settings"
        alert.informativeText = ChatKitSettings.signingKey(in: appSupportDirectory) == nil
            ? "No signing key is saved. Enter one to enable ChatKit sessions. Orcheo will restart its local services after saving it."
            : "A signing key is saved. Enter a replacement key, or remove the saved key. Orcheo will restart its local services after the change."
        alert.addButton(withTitle: "Save and Restart")
        alert.addButton(withTitle: "Remove Key and Restart")
        alert.addButton(withTitle: "Cancel")

        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        field.placeholderString = "Session token signing key"
        alert.accessoryView = field

        switch alert.runModal() {
        case .alertFirstButtonReturn:
            do {
                try ChatKitSettings.saveSigningKey(field.stringValue, in: appSupportDirectory)
                restartAfterSettingsChange()
            } catch {
                showError(error)
            }
        case .alertSecondButtonReturn:
            do {
                try ChatKitSettings.removeSigningKey(in: appSupportDirectory)
                restartAfterSettingsChange()
            } catch {
                showError(error)
            }
        default:
            return
        }
    }

    private func restartAfterSettingsChange() {
        loadStatusPage(title: "Applying ChatKit Settings", detail: "Restarting local services...")
        Task {
            do {
                let url = try await supervisor.restart()
                webView?.load(URLRequest(url: url))
            } catch {
                showError(error)
            }
        }
    }

    private func chatKitSettingsDirectory() -> URL? {
        if let configured = supervisor.configuration?.appSupportDirectory {
            return configured
        }
        return try? FileManager.default.ensureDirectory(
            base: .applicationSupportDirectory,
            component: "com.orcheo.desktop"
        )
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
        // The local backend serves Studio without explicit Cache-Control
        // headers, and this app's bundle identifier (hence WKWebView's
        // on-disk cache) stays the same across every rebuild while the
        // backend port can also repeat. Without this, WebKit can keep
        // serving an old cached index.html (pointing at stale hashed
        // asset filenames) after a rebuild changes Studio's UI.
        Self.clearWebViewHTTPCache(in: configuration.websiteDataStore)
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
        window.minSize = NSSize(width: 960, height: 640)
        window.contentView = webView
        window.delegate = self
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

    private static func clearWebViewHTTPCache(in dataStore: WKWebsiteDataStore) {
        let cacheTypes: Set<String> = [WKWebsiteDataTypeDiskCache, WKWebsiteDataTypeMemoryCache]
        dataStore.removeData(ofTypes: cacheTypes, modifiedSince: .distantPast) {}
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

    // Mirrors the Tauri shell's menu bar: Orcheo (About, ChatKit Settings,
    // Services, Quit), Edit, and Window.
    private func buildMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(targetedItem(title: "About Orcheo", action: #selector(showAbout), keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(targetedItem(title: "ChatKit Settings...", action: #selector(openChatKitSettings), keyEquivalent: ","))
        appMenu.addItem(.separator())

        let servicesItem = NSMenuItem()
        let servicesMenu = NSMenu(title: "Services")
        servicesMenu.addItem(targetedItem(title: "Reload Studio", action: #selector(reloadStudio), keyEquivalent: "r"))
        servicesMenu.addItem(targetedItem(title: "Restart Local Services", action: #selector(restartServices), keyEquivalent: ""))
        servicesMenu.addItem(targetedItem(title: "Open Logs", action: #selector(openLogs), keyEquivalent: "l"))
        servicesItem.title = "Services"
        servicesItem.submenu = servicesMenu
        appMenu.addItem(servicesItem)

        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Quit Orcheo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        mainMenu.addItem(editMenuItem())
        mainMenu.addItem(windowMenuItem())

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

    private func windowMenuItem() -> NSMenuItem {
        let windowMenuItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")

        windowMenu.addItem(responderItem(title: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"))
        windowMenu.addItem(responderItem(title: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: ""))
        let fullScreen = responderItem(
            title: "Toggle Full Screen",
            action: #selector(NSWindow.toggleFullScreen(_:)),
            keyEquivalent: "f"
        )
        fullScreen.keyEquivalentModifierMask = NSEvent.ModifierFlags([.command, .control])
        windowMenu.addItem(fullScreen)

        windowMenuItem.submenu = windowMenu
        return windowMenuItem
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
        alert.addButton(withTitle: "Retry")
        alert.addButton(withTitle: "Open Logs")
        alert.addButton(withTitle: "Close")
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            restartServices()
        case .alertSecondButtonReturn:
            openLogs()
        default:
            break
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
