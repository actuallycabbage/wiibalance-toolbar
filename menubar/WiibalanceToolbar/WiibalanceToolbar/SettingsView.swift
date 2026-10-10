import SwiftUI
import AppKit

struct SettingsView: View {
    let server: WiiServer
    @State private var projectRoot = AppSettings.projectRoot
    @State private var pythonPath = AppSettings.pythonPath
    @State private var showAdvanced = false
    @State private var showDevices = false
    @State private var showDiagnostics = false

    var body: some View {
        Form {
            Section("Connection") {
                Text("The connection starts when WiiBalance opens and stays running when you close the menu.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Text(server.statusLabel).foregroundStyle(.secondary)
                    Spacer()
                    Button(server.isActive ? "Stop" : "Start") {
                        if server.isActive { server.stop() } else { server.start() }
                    }
                    .disabled(server.phase == .stopping)
                }
                Button("Adapters and devices…") { showDevices = true }
                Button("Diagnostics…") { showDiagnostics = true }
            }
            Section {
                DisclosureGroup("Advanced", isExpanded: $showAdvanced) {
                    VStack(alignment: .leading, spacing: 14) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Project folder")
                            HStack {
                                TextField("wiibalance-toolbar checkout", text: $projectRoot)
                                    .labelsHidden()
                                    .accessibilityLabel("Project folder")
                                Button("Choose…") { chooseFolder() }
                            }
                        }
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Python executable")
                            HStack {
                                TextField("Default: .venv/bin/python in project", text: $pythonPath)
                                    .labelsHidden()
                                    .accessibilityLabel("Python executable")
                                Button("Choose…") { choosePython() }
                            }
                        }
                        Text("Leave the executable blank to use the project’s environment. Run ‘uv sync’ in the project folder if it needs setting up. Changes apply the next time you start.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(.top, 8)
                }
            }
        }
        .formStyle(.grouped)
        .textFieldStyle(.roundedBorder)
        .padding()
        .frame(width: 560, height: showAdvanced ? 540 : 340)
        .sheet(isPresented: $showDevices) {
            VStack {
                ScrollView { ConnectionView(server: server).padding() }
                Button("Done") { showDevices = false }.padding()
            }.frame(width: 430, height: 560)
        }
        .sheet(isPresented: $showDiagnostics) {
            VStack {
                ScrollView { DiagnosticsView(server: server).padding() }
                Button("Done") { showDiagnostics = false }.padding()
            }.frame(width: 520, height: 440)
        }
        .onChange(of: projectRoot) { _, value in AppSettings.projectRoot = value }
        .onChange(of: pythonPath) { _, value in AppSettings.pythonPath = value }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = "Choose the WiiBalance project folder."
        if panel.runModal() == .OK, let url = panel.url { projectRoot = url.path }
    }

    private func choosePython() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        panel.message = "Choose the Python executable with this project’s dependencies installed."
        if panel.runModal() == .OK, let url = panel.url { pythonPath = url.path }
    }
}
