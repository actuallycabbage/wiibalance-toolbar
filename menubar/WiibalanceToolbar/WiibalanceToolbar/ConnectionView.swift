import SwiftUI

struct ConnectionView: View {
    @Bindable var server: WiiServer
    @State private var address = ""

    private var validAddress: Bool {
        address.range(of: "^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}(/P)?$", options: .regularExpression) != nil
    }

    private func enabled(_ method: String) -> Bool {
        server.canSend && server.supports(method)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if server.phase != .ready {
                Label(server.phase == .legacy ? "Reconnect your board" : "Connect your Balance Board", systemImage: "info.circle")
                    .font(.subheadline.weight(.medium))
                Text(server.phase == .legacy
                     ? "Press its power button to reconnect. New pairing and adapter selection aren’t available yet."
                     : "Use an external USB Bluetooth adapter, then choose Start.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            GroupBox {
                VStack(alignment: .leading, spacing: 10) {
                    if server.adapters.isEmpty {
                        Text(server.supports("adapter_list") ? "No adapters found. Plug one in and refresh." : "Adapter list unavailable.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        Picker("Adapter", selection: $server.selectedTransport) {
                            Text("Choose an adapter").tag("")
                            if !server.selectedTransport.isEmpty,
                               !server.adapters.contains(where: { $0.transport == server.selectedTransport }) {
                                Text("Saved adapter unavailable").tag(server.selectedTransport)
                            }
                            ForEach(server.adapters) { adapter in
                                Text("\(adapter.name) — \(adapter.serial ?? adapter.transport)").tag(adapter.transport)
                            }
                        }
                        .labelsHidden()
                        .disabled(server.radio.state != .stopped || !server.canSend)
                        if let adapter = server.adapters.first(where: { $0.transport == server.selectedTransport }) {
                            Text([adapter.manufacturer, adapter.serial].compactMap { $0 }.joined(separator: " · "))
                                .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        }
                    }
                    HStack {
                        Button("Refresh", systemImage: "arrow.clockwise") { server.request("adapter_list") }
                            .disabled(!enabled("adapter_list") || server.pairing.isBusy)
                        Spacer()
                        if server.radio.state == .stopped {
                            Button("Use adapter") {
                                server.request("server_start", params: ["transport": server.selectedTransport])
                            }
                            .disabled(!enabled("server_start") || !server.adapters.contains(where: { $0.transport == server.selectedTransport }))
                        } else {
                            Button("Release adapter") { server.request("server_stop") }
                                .disabled(!enabled("server_stop"))
                        }
                    }
                    if let transport = server.radio.transport {
                        Text("Active: \(transport)").font(.caption).foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(4)
            } label: {
                Label("1. Bluetooth adapter", systemImage: "cable.connector")
            }

            GroupBox {
                VStack(alignment: .leading, spacing: 12) {
                    Text("For a new board, open the battery cover and press the red SYNC button. Keep its blue light flashing while searching or pairing. For a saved board, press its power button.")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("Find boards", systemImage: "magnifyingglass") { server.request("wiimote_scan") }
                            .disabled(!enabled("wiimote_scan") || server.radio.state != .listening || server.pairing.isBusy)
                        Spacer()
                        Button("Refresh saved") { server.request("wiimote_list") }
                            .disabled(!enabled("wiimote_list") || server.pairing.isBusy)
                    }
                    if server.pairing.isBusy {
                        HStack {
                            ProgressView().controlSize(.small)
                            Text(server.pairing.message ?? (server.pairing.state == .scanning ? "Searching for boards…" : "Pairing…"))
                                .font(.caption)
                            Spacer()
                            Button("Cancel") { server.request("wiimote_cancel_pairing") }
                                .disabled(!enabled("wiimote_cancel_pairing"))
                        }
                    } else if let message = server.pairing.message {
                        Text(message).font(.caption).foregroundStyle(.secondary)
                    }

                    if server.devices.isEmpty {
                        Text("No boards to show yet.").font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(server.devices) { device in
                        deviceRow(device)
                    }

                    DisclosureGroup("Pair by Bluetooth address") {
                        VStack(alignment: .leading, spacing: 8) {
                            TextField("00:25:A0:41:39:0A", text: $address)
                                .textFieldStyle(.roundedBorder)
                                .accessibilityLabel("Board Bluetooth address")
                            Button("Pair board") {
                                server.request("wiimote_pair", params: ["address": address.uppercased()])
                            }
                            .disabled(!enabled("wiimote_pair") || !validAddress || server.radio.state != .listening || server.pairing.isBusy || server.activeBoard != nil)
                        }
                        .padding(.top, 6)
                    }
                    .font(.caption)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(4)
            } label: {
                Label("2. Balance Board", systemImage: "figure.stand")
            }

            if server.pendingMethod != nil {
                ProgressView("Updating devices…")
                    .controlSize(.small)
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func deviceRow(_ device: WiiDevice) -> some View {
        HStack(spacing: 8) {
            Image(systemName: device.isBalanceBoard ? "figure.stand" : "gamecontroller")
                .foregroundStyle(device.connected ? Color.accentColor : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(device.name).font(.subheadline)
                Text(device.address).font(.system(.caption2, design: .monospaced))
                Text(device.connected ? "Connected" : (device.paired ? "Saved · press the power button" : "Discovered"))
                    .font(.caption2)
            }
            Spacer(minLength: 4)
            if device.connected {
                Button("Disconnect") { server.request("wiimote_disconnect", params: ["address": device.address]) }
                    .disabled(!enabled("wiimote_disconnect"))
            } else if device.isBalanceBoard && !device.paired {
                Button("Pair") { server.request("wiimote_pair", params: ["address": device.address]) }
                    .disabled(!enabled("wiimote_pair") || server.radio.state != .listening || server.pairing.isBusy || server.activeBoard != nil)
            }
        }
    }
}
