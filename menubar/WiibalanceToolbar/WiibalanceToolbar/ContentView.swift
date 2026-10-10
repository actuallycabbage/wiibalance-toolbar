import SwiftUI

struct ContentView: View {
    let server: WiiServer
    let openSettings: () -> Void
    @State private var pairNew = false

    var body: some View {
        VStack(spacing: 14) {
            ConnectionHeader(server: server)

            if let error = server.lastError {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    Text(error).font(.caption).textSelection(.enabled)
                    Spacer(minLength: 0)
                    Button { server.lastError = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Dismiss error")
                }
                .padding(10)
                .background(.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
            }

            // Preserve the measured layout while closed; inserting it during
            // popoverWillShow makes AppKit first position a collapsed popover.
            VStack(spacing: 14) {
                if server.hasConnectedBoard || server.sample != nil {
                    BalanceView(server: server)
                } else if pairNew || server.pairing.isBusy {
                    Image("BoardSync")
                        .resizable().scaledToFit().frame(height: 130)
                        .accessibilityLabel("Underside of the Balance Board, with the red SYNC button inside the open battery compartment")
                    Text("Pair a new board").font(.headline)
                    Text("Turn the board over, open the battery cover and press the red SYNC button. Keep the blue light flashing.")
                        .font(.subheadline).foregroundStyle(.secondary)
                    switch server.pairing.state {
                    case .scanning:
                        ProgressView("Looking for your board…").controlSize(.small)
                    case .pairing:
                        ProgressView("Pairing your board…").controlSize(.small)
                    case .failed:
                        Text(server.pairing.message ?? "Pairing failed. Press SYNC and try again.")
                            .font(.caption).foregroundStyle(.secondary)
                        Button("Try again") { server.beginPairing() }
                            .disabled(!server.canSend)
                    case .idle:
                        if server.phase == .starting || server.radio.state == .starting || server.pendingMethod != nil {
                            ProgressView("Preparing connection…").controlSize(.small)
                        } else if server.supports("wiiboard_pair") {
                            Button("Find my board") { server.beginPairing() }
                                .disabled(!server.canSend)
                        } else {
                            Text("Pairing is unavailable with this server.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Button("Back") {
                        server.cancelPairing()
                        pairNew = false
                    }
                } else {
                    Image("BoardWake")
                        .resizable().scaledToFit().frame(height: 130)
                        .accessibilityLabel("Wireframe Balance Board viewed at a shallow angle from the front, with its POWER button highlighted")
                    Text(server.isActive ? "Wake your board" : "Ready when you are").font(.headline)
                    Text(server.isActive ? "Press the board’s front POWER button to reconnect." : "Switch on above, then press the board’s front POWER button.")
                        .font(.subheadline).foregroundStyle(.secondary)
                    Button("Pair a new board…") {
                        pairNew = true
                        server.beginPairing()
                    }
                }
            }
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)

            Divider()
            HStack {
                Button(action: openSettings) { Label("Settings…", systemImage: "gearshape") }
                Spacer()
                Button("Quit") { NSApplication.shared.terminate(nil) }
                    .keyboardShortcut("q")
            }
            .buttonStyle(.plain)
            .font(.caption)
        }
        .padding(16)
        .frame(width: 390)
        .fixedSize(horizontal: false, vertical: true)
        .transaction { $0.animation = nil }
        .onChange(of: server.activeBoard?.address) { _, address in
            if address != nil { pairNew = false }
        }
    }
}

// Separate observation boundaries keep 30 Hz balance updates and log traffic from
// rebuilding tab controls and the device list while the user is interacting with them.
private struct ConnectionHeader: View {
    let server: WiiServer

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text("wiiboard").font(.title2.weight(.semibold))
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Circle().fill(statusColor).frame(width: 7, height: 7)
                        .alignmentGuide(.firstTextBaseline) { dimensions in
                            dimensions[VerticalAlignment.center] + NSFont.preferredFont(forTextStyle: .caption1).capHeight / 2
                        }
                    Text(server.statusLabel).font(.caption).foregroundStyle(.secondary)
                    ConnectionBattery(server: server)
                        .alignmentGuide(.firstTextBaseline) { dimensions in
                            dimensions[VerticalAlignment.center] + NSFont.preferredFont(forTextStyle: .caption1).capHeight / 2
                        }
                }
            }
            Spacer()
            if server.phase == .starting || server.phase == .stopping {
                ProgressView().controlSize(.small)
            }
            Toggle("Connection", isOn: Binding(
                get: { server.isActive && server.phase != .stopping },
                set: { enabled in
                    if enabled { server.start() } else { server.stop() }
                }
            ))
            .toggleStyle(.switch)
            .labelsHidden()
            .disabled(server.phase == .stopping)
            .help("Start or stop the connection to your board")
        }
    }

    private var statusColor: Color {
        switch server.statusTone {
        case .inactive, .error: return .red
        case .connected: return .green
        case .busy: return .yellow
        }
    }
}

private struct ConnectionBattery: View {
    let server: WiiServer

    var body: some View {
        if let sample = server.sample,
           let bars = sample.batteryBars ?? sample.batteryPercent.map({ min(4, ($0 + 24) / 25) }) {
            Image("Battery\(bars)")
                .renderingMode(.template)
                .resizable()
                .scaledToFit()
                .frame(width: 22.5, height: 10)
                .foregroundStyle(bars == 0 ? Color.red : (bars == 1 ? Color.orange : Color.secondary))
                .accessibilityLabel("Battery: \(bars) of 4 bars")
                .help("Battery: \(bars) of 4 bars")
        }
    }
}

private struct BalanceView: View {
    let server: WiiServer

    var body: some View {
        VStack(spacing: 14) {
            Group {
                if server.isPopoverVisible {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let recent = server.sampleDate.map { context.date.timeIntervalSince($0) < 3 } ?? false
                        BalanceBoardView(sample: server.sample, recent: recent)
                    }
                } else {
                    BalanceBoardView(sample: server.sample, recent: false)
                }
            }
            .frame(height: 190)
            .help("A dimmed marker shows an older reading.")

            if let sample = server.sample {
                let right = Int((sample.unitX * 100).rounded())
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Left / right").font(.caption).foregroundStyle(.secondary)
                        Text("\(100 - right)% / \(right)%")
                            .monospacedDigit()
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text("Weight").font(.caption).foregroundStyle(.secondary)
                        if let weight = sample.weightKg {
                            Text("\(weight, specifier: "%.1f") kg").monospacedDigit()
                        } else {
                            Text("Not reported").foregroundStyle(.secondary)
                        }
                    }
                }
            } else {
                Text("Your centre of balance will appear here.")
                    .font(.subheadline)
                Text("Step onto the board to see your balance.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

struct DiagnosticsView: View {
    let server: WiiServer

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            LabeledContent("Protocol", value: server.phase == .ready ? "NDJSON v1" : "Legacy / awaiting handshake")
                .font(.caption)
            Text("Recent connection and app activity.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Copy log", systemImage: "doc.on.doc") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(server.diagnostics, forType: .string)
                }
                Button("Clear") { server.clearDiagnostics() }
                Spacer()
            }
            .disabled(server.diagnostics.isEmpty)
            Text(server.diagnostics.isEmpty ? "No messages yet." : server.diagnostics)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct BalanceBoardView: View {
    let sample: BalanceSample?
    var recent = true

    var body: some View {
        VStack(spacing: 6) {
            Text("FRONT").font(.system(size: 9, weight: .medium)).foregroundStyle(.secondary)
            Canvas { context, size in
                let board = CGRect(x: 12, y: 8, width: size.width - 24, height: size.height - 16)
                let outline = Path(roundedRect: board, cornerRadius: 12)
                context.fill(outline, with: .color(.secondary.opacity(0.05)))
                context.stroke(outline, with: .color(.secondary.opacity(0.5)), lineWidth: 1.5)
                var cross = Path()
                cross.move(to: CGPoint(x: board.midX, y: board.minY + 8))
                cross.addLine(to: CGPoint(x: board.midX, y: board.maxY - 8))
                cross.move(to: CGPoint(x: board.minX + 8, y: board.midY))
                cross.addLine(to: CGPoint(x: board.maxX - 8, y: board.midY))
                context.stroke(cross, with: .color(.secondary.opacity(0.25)), style: StrokeStyle(lineWidth: 1, dash: [3, 4]))

                if let sample {
                    let x = board.minX + board.width * sample.unitX
                    let y = board.minY + board.height * sample.unitY
                    let dot = Path(ellipseIn: CGRect(x: x - 7, y: y - 7, width: 14, height: 14))
                    context.fill(dot, with: .color(recent ? .accentColor : .secondary))
                }
            }
            HStack {
                Text("LEFT")
                Spacer()
                Text("BACK")
                Spacer()
                Text("RIGHT")
            }
            .font(.system(size: 9, weight: .medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Centre of balance")
        .accessibilityValue(sample.map {
            "\(Int($0.unitX * 100)) percent right, \(Int((1 - $0.unitY) * 100)) percent front. \(recent ? "Recent reading" : "Last reading")"
        } ?? "No reading")
    }
}

#Preview {
    let server = WiiServer()
    ContentView(server: server, openSettings: {})
        .onAppear { server.setPopoverVisible(true) }
}
