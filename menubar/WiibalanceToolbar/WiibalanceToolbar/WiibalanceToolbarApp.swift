import AppKit
import SwiftUI
import Combine

struct MenuBarPosition {
    // Fraction of the full board width/height, compared with the last drawn point.
    // Keeping that reference fixed lets several small movements accumulate visibly.
    static let movementThreshold = 0.02

    let x: Double
    let y: Double
    let recent: Bool

    func needsRedraw(after previous: Self?) -> Bool {
        guard let previous else { return true }
        return recent != previous.recent ||
            abs(x - previous.x) >= Self.movementThreshold ||
            abs(y - previous.y) >= Self.movementThreshold
    }
}

@main
enum WiibalanceToolbarApp {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        withExtendedLifetime(delegate) { app.run() }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    let server = WiiServer()
    let popover = NSPopover()
    private var statusItem: NSStatusItem?
    private(set) var settingsWindow: NSWindow?
    private var menuBarTimer: AnyCancellable?
    private let stoppedImage = AppDelegate.stateImage(waiting: false)
    private let waitingImage = AppDelegate.stateImage(waiting: true)
    private var lastDrawnState: WiiServer.MenuBarState?

    private static func stateImage(waiting: Bool) -> NSImage {
        let image = NSImage(size: NSSize(width: 28, height: 18), flipped: true) { _ in
            NSColor.black.setStroke()
            let board = NSBezierPath(roundedRect: NSRect(x: 1, y: 2, width: 26, height: 14), xRadius: 3, yRadius: 3)
            board.lineWidth = 1
            board.stroke()
            let marker = NSBezierPath()
            if waiting {
                NSColor.black.setFill()
                for x in [8.0, 13.0, 18.0] {
                    NSBezierPath(ovalIn: NSRect(x: x, y: 8, width: 2, height: 2)).fill()
                }
            } else {
                marker.move(to: NSPoint(x: 11, y: 6))
                marker.line(to: NSPoint(x: 17, y: 12))
                marker.move(to: NSPoint(x: 17, y: 6))
                marker.line(to: NSPoint(x: 11, y: 12))
            }
            marker.lineWidth = 1.2
            marker.lineCapStyle = .round
            marker.lineJoinStyle = .round
            marker.stroke()
            return true
        }
        image.isTemplate = true
        return image
    }
    private var lastDrawnPosition: MenuBarPosition?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // SwiftUI's App scene normally supplies these. An AppKit-hosted text field
        // needs the Edit menu's key equivalents for standard clipboard shortcuts.
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit WiiBalance", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(withTitle: "WiiBalance", action: nil, keyEquivalent: "").submenu = appMenu
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(withTitle: "Edit", action: nil, keyEquivalent: "").submenu = editMenu
        NSApp.mainMenu = menu

        // MenuBarExtra owns its presentation timing. Keep one hosting controller and
        // use AppKit here so opening needs neither a new SwiftUI tree nor an animation.
        popover.animates = false
        popover.behavior = .transient
        popover.delegate = self
        popover.contentViewController = NSHostingController(rootView:
            ContentView(server: server, openSettings: { [weak self] in self?.showSettings() })
        )
        popover.contentViewController?.view.layoutSubtreeIfNeeded()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem?.button?.target = self
        statusItem?.button?.action = #selector(togglePopover)
        statusItem?.button?.sendAction(on: [.leftMouseDown])
        statusItem?.button?.image = stoppedImage
        updateStatusItem()
        // Poll the latest reading at 5 Hz instead of observing every sensor update.
        // The same tick handles ageing; unchanged position/freshness never redraws.
        menuBarTimer = Timer.publish(every: 0.2, on: .main, in: .common)
            .autoconnect().sink { [weak self] _ in
                MainActor.assumeIsolated { self?.updateStatusItem() }
            }

        if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil {
            server.start()
            updateStatusItem()
        }
    }

    @objc func togglePopover() {
        guard let button = statusItem?.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            // Catch up the paused sample and settle SwiftUI sizing before AppKit
            // chooses the popover's screen position, not inside its show callback.
            server.setPopoverVisible(true)
            popover.contentViewController?.view.layoutSubtreeIfNeeded()
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            if !popover.isShown { server.setPopoverVisible(false) }
            NSApp.activate(ignoringOtherApps: true)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    func popoverWillShow(_ notification: Notification) {
        server.setPopoverVisible(true)
    }

    func popoverDidClose(_ notification: Notification) {
        // This also covers Escape and clicks outside the transient popover.
        server.setPopoverVisible(false)
    }

    func showSettings() {
        popover.performClose(nil)
        if settingsWindow == nil {
            let window = NSWindow(contentViewController: NSHostingController(rootView: SettingsView(server: server)))
            window.title = "WiiBalance Settings"
            window.styleMask = [.titled, .closable]
            window.isReleasedWhenClosed = false
            window.center()
            settingsWindow = window
        }
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }

    private func updateStatusItem() {
        guard let button = statusItem?.button else { return }
        let label = "WiiBalance: \(server.statusLabel)"
        if button.toolTip != label {
            button.toolTip = label
            button.setAccessibilityLabel(label)
        }
        let state = server.menuBarState
        guard state == .balance, let reading = server.currentReading else {
            if lastDrawnState != state {
                button.image = state == .waiting ? waitingImage : stoppedImage
                lastDrawnPosition = nil
                lastDrawnState = state
            }
            return
        }
        let position = MenuBarPosition(
            x: reading.sample.unitX, y: reading.sample.unitY,
            recent: Date().timeIntervalSince(reading.date) < 3
        )
        guard position.needsRedraw(after: lastDrawnPosition) else { return }
        lastDrawnPosition = position
        lastDrawnState = .balance
        let image = NSImage(size: NSSize(width: 28, height: 18), flipped: true) { _ in
            NSColor.black.setStroke()
            let board = NSBezierPath(roundedRect: NSRect(x: 1, y: 2, width: 26, height: 14), xRadius: 3, yRadius: 3)
            board.lineWidth = 1
            board.stroke()
            NSColor.black.withAlphaComponent(position.recent ? 1 : 0.4).setFill()
            NSBezierPath(ovalIn: NSRect(x: 1 + 22 * position.x, y: 2 + 10 * position.y, width: 4, height: 4)).fill()
            return true
        }
        image.isTemplate = true
        button.image = image
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard server.isActive else { return .terminateNow }
        // Keep the app alive until Python releases the adapter (or the bounded
        // shutdown kills it). Closing the popover never stops the helper.
        server.stop { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}
