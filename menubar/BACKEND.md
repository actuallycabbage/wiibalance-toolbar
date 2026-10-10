# Python integration and Swift contract

## Connection status and legacy compatibility

Swift uses structured adapter/device/pairing events and pending commands for its
shared header, Settings and menu-bar status. Connection is independent of whether
the board has produced a balance reading.

For the existing binary-only server, Swift temporarily recognises complete stderr
lines `[connect] WiiBoard` and `timeout waiting for hci reset, retrying`. These show
“Connected · waiting for readings” and “Retrying adapter…” respectively. Other logs
are diagnostic text only. Without an explicit signal, it shows “Waiting for server
updates”; silence does not establish whether Bluetooth connected or disconnected.

The Swift app now launches `python -m app.server.server --json`, which emits
structured startup/listening, connection/disconnection and pairing progress events
independently of CoB changes. The compatibility path above is only for old helpers;
it cannot detect physical disconnects while the process remains running.

## New-board pairing

“Pair a new board” starts the helper/adapter if necessary, then sends `wiiboard_pair`.
Python runs Classic inquiry until it finds `Nintendo RVL-WBC-01`, resolving remote
names when inquiry data omits them. It stops inquiry, authenticates using the existing
host-address PIN/bonding support, opens HID channels and initialises the board.
Register writes must receive successful ACKs and calibration must pass its CRC
before a connected snapshot is published. Bumble persists link keys in the checkout's
`wiimote-keys.json`. Initialisation success is not proof that the physical sensors
are producing useful weight measurements.

Discovery continues until a board is found, Back is pressed, or the app is switched
off. Pairing itself is limited to 30 seconds. Closing just the popover keeps the
operation running. The red SYNC button is inside the battery compartment underneath;
the front POWER button reconnects an already bonded board.

`app/server/control.py` implements the commands it advertises. Optional methods
below remain capability-gated; unsupported controls stay disabled.

## What exists today

- Without `--json`, `app/server/server.py` retains the old balance-only FlatBuffers stream.
- With `--json`, `app/server/control.py` handles stdin commands, asynchronous operations and state snapshots. Readiness precedes USB startup; EOF and signals cancel work and close the transport.
- Adapter enumeration reuses `app/usb_probe.py`. The Swift power switch starts the selected transport (default `usb:0`); Settings can release/select another adapter.
- `wiimote/server.py` handles discovery, bonding, initialisation, inbound reconnects and disconnect callbacks.
- `app/server/protocol.py` remains an unused earlier model sketch; the running control plane uses standard-library JSON.

## Transport and handshake

Keep stderr for human-readable logs. In the new mode, stdin **and** stdout carry UTF-8 NDJSON: one JSON object per line, no banners, no leading whitespace before the first `{`, and flush every message. JSON uses snake_case keys. Limit each line to 64 KiB. Both pipes must be serviced without blocking the Bluetooth event loop.

The first stdout message must arrive within three seconds, before initialising USB:

```json
{"type":"event","event":"ready","data":{"protocol_version":1,"capabilities":["adapter_list","server_status","server_start","server_stop","wiimote_list","wiiboard_pair","wiimote_pair","wiimote_cancel_pairing","wiimote_disconnect"]}}
```

Advertise **only implemented methods**. The UI disables everything else. After `ready`, emit initial `server_status` and `devices` snapshots. Adapter enumeration runs on demand and works before a transport is opened. Do not automatically claim `usb:0` in the new mode; wait for `server_start`.

The client auto-detects the existing binary stream (four-byte **big-endian** length followed by a FlatBuffers `ServerEnvelope`, maximum 1 MiB) and continues to display it. A process must use one stdout format for its whole lifetime. In NDJSON mode send balance events as JSON too; do not interleave raw FlatBuffers with JSON. Once Python has migrated and compatibility is no longer needed, the legacy decoder/generated bindings/FlatBuffers dependency can be removed together.

## Commands

```json
{"id":1,"method":"server_start","params":{"transport":"usb:2357:0604/0CEF1518A7E6"}}
{"id":2,"method":"wiimote_pair","params":{"address":"00:25:A0:41:39:0A"}}
```

The client limits command lines to 4 KiB (including the newline) to keep its single
outstanding command below pipe capacity. Transport identifiers and addresses should
be short identifiers, not embedded configuration documents.

Every command gets exactly one acknowledgement with the same `id`, within ten seconds:

```json
{"type":"response","id":1,"ok":true}
{"type":"response","id":2,"ok":false,"error":"The board did not respond. Press its red SYNC button and try again."}
```

An acknowledgement means **accepted**, not paired or connected. Actual state comes from events. For async operations, emit the initial `starting`/`scanning`/`pairing` state **before** the success acknowledgement; then emit completion/failure events. This prevents a second click after acknowledgement from starting a duplicate operation. The UI permits one outstanding acknowledgement, and stops an unresponsive helper instead of retrying an operation with an unknown outcome. Unknown or invalid requests should return a useful error, not kill the command loop.

| Method / capability | Parameters | Events / expected behaviour |
| --- | --- | --- |
| `adapter_list` | `{}` | Emit complete `adapters` snapshot; use stable serial-based transport names where available. |
| `server_status` | `{}` | Emit current `server_status`. |
| `server_start` | `transport` | Emit `starting`, initialise the chosen transport, then `listening` or `stopped` plus an `error` event. |
| `server_stop` | `{}` | Cancel discovery/pairing, disconnect devices, close transport, emit stopped/empty connected state. Keep the helper alive for further commands. |
| `wiimote_list` | `{}` | Emit complete `devices` snapshot of devices known this session. Saved-device metadata enumeration is still a TODO. |
| `wiimote_scan` (not implemented) | `{}` | Optional browse-only discovery; not advertised by the current helper. |
| `wiiboard_pair` | `{}` | Keep scanning for a Balance Board, then automatically pair and initialise the first one. Cancel with `wiimote_cancel_pairing`. |
| `wiimote_pair` | `address` | Accept a classic address, optionally with Bumble's `/P` suffix. Emit `pairing:pairing`, authenticate and initialise, persist keys, then emit connected `devices` and `pairing:idle`. On failure emit `pairing:failed`. |
| `wiimote_cancel_pairing` | `{}` | Cancel the current scan/pair attempt, close partial connections, emit `pairing:idle`. A cancelled attempt must not later emit success. |
| `wiimote_disconnect` | `address` | Disconnect the device, update `devices`, preserve its saved key. |

Discovery has no overall deadline: the user may need time to find and press SYNC.
After selection, Python limits pairing to 30 seconds; Swift retains a 60-second
watchdog for the `pairing` state only. Progress messages cannot extend that deadline.
Always report a terminal state, including on cancellation or exceptions.

## Events

Events and successful command results use snapshots rather than deltas. Arrays replace the previous list and identifiers must be unique. Publish changes caused by hardware too, not just UI commands.

### Adapters

```json
{"type":"event","event":"adapters","data":{"adapters":[{"transport":"usb:2357:0604/0CEF1518A7E6","name":"TP-Link UB500","manufacturer":"TP-Link","serial":"0CEF1518A7E6"}]}}
```

`transport` and `name` are required; `manufacturer` and `serial` may be omitted/null. Report Bluetooth HCI adapters, not every USB peripheral. An empty array means no adapters found. Refresh must handle unplugging and USB permission errors without confusing them with an empty successful result.

### Radio state

```json
{"type":"event","event":"server_status","data":{"state":"listening","transport":"usb:2357:0604/0CEF1518A7E6"}}
```

`state`: `stopped`, `starting`, or `listening`. `transport` may be null while stopped. `listening` means transport and inbound listener initialisation completed, **not** that a board is connected. On adapter removal emit stopped and an actionable error. The reported active transport is authoritative; Swift's saved selection is only a preference.

### Devices

```json
{"type":"event","event":"devices","data":{"devices":[{"address":"00:25:A0:41:39:0A","name":"Wii Balance Board","kind":"balance_board","paired":true,"connected":true}]}}
```

All fields are required. `kind` is `balance_board` or `wiimote` (unknown kinds are displayed but cannot be paired as boards). Addresses must be normalised consistently across snapshots, pairing commands and balance events. Emit connected state only after board initialisation/calibration succeeds. On disconnection set `connected:false` or remove an unsaved device; don't wait for another sample. Include previously found/saved devices as appropriate. The toolbar displays the first connected Balance Board; multi-board selection is not implemented.

### Pairing / discovery progress

```json
{"type":"event","event":"pairing","data":{"state":"pairing","address":"00:25:A0:41:39:0A","message":"Authenticating the board…"}}
{"type":"event","event":"pairing","data":{"state":"failed","address":"00:25:A0:41:39:0A","message":"Board timed out. Press the red SYNC button and try again."}}
```

`state`: `idle`, `scanning`, `pairing`, `failed`. `address` and `message` are optional. Send connected `devices` before the final `idle` success event. A saved board reconnects by pressing its power button while the server listens; that incoming connection must emit the same device state events.

### Balance

```json
{"type":"event","event":"balance","data":{"address":"00:25:A0:41:39:0A","x":-12.5,"y":25.0,"weight_kg":72.4,"battery_percent":80}}
```

`address`, `x`, `y` required. `weight_kg`, `battery_percent` and `battery_bars` optional/null. All numbers finite; weight nonnegative, battery percentage an integer 0–100 and bars an integer 0–4. The current helper uses report `0x34` and sends bars using WiiBrew's extension-byte thresholds, rather than estimating a percentage. Diagnostics also logs the separate status-report battery byte and low-battery flag. Coordinates follow `WiiBoard`: -100…100, positive X right, positive Y front. Swift clamps display coordinates and flips Y for screen space. Emit `devices` with the connected board and `server_status:listening` before samples. The client ignores samples from boards it does not know to be connected.

Send a current sample at least once per second while reports are arriving, even if balance hasn't changed, so the UI can distinguish a fresh stable reading from an old one. Do not manufacture fresh samples from a cached reading after Bluetooth reports stop. Decide how to report an unloaded board correctly in Python; the UI should not guess occupancy from centre of balance alone.

### Asynchronous errors

```json
{"type":"event","event":"error","data":{"message":"USB adapter disconnected. Reconnect it and choose Refresh."}}
```

Publish corrected state snapshots alongside errors. Use the response error for a rejected command; this event is for errors after acknowledgement or unsolicited hardware failures. Include the exception/traceback on stderr for Diagnostics. Unknown future event names are ignored; malformed known messages fail the session.

## Remaining work

- [ ] Persist/enumerate saved-device names/types alongside Bumble's link keys.
- [ ] Expose battery readings and adapter unplug/retry state in the control plane.
- [ ] Diagnose the all-zero sensor reports; add tare/unloaded-board behaviour.
- [ ] Verify cold pairing, saved reconnect, cancellation, board power-off, adapter unplug/replug and start/stop cycles with hardware.

Run hardware-free checks with `.venv/bin/python -m unittest discover -s tests`
and `make -C menubar test`.

## Swift follow-ups after the backend lands

- [ ] Test the full hardware flow against these events (the automated Swift checks use protocol fixtures and subprocesses without Bluetooth).
- [ ] Remove legacy compatibility when it is no longer useful.
- [ ] Decide on distribution of Python/firmware before calling the app standalone.
