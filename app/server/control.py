"""NDJSON control plane used by the Swift toolbar."""

import asyncio
import json
import re
import signal
import sys
import time
import traceback
from contextlib import suppress

import usb1

from app.usb_probe import derive_device_info
from wiimote.server import WiimoteServer
from wiimote.wiiboard import WiiBoard


class Controller:
    capabilities = [
        "adapter_list",
        "server_status",
        "server_start",
        "server_stop",
        "wiimote_list",
        "wiiboard_pair",
        "wiimote_pair",
        "wiimote_cancel_pairing",
        "wiimote_disconnect",
    ]

    def __init__(self, send, server_factory=WiimoteServer):
        self.send = send
        self.server_factory = server_factory
        self.server = None
        self.operation = None
        self.radio = {"state": "stopped", "transport": None}
        self.devices = {}
        self.pairing = {"state": "idle"}
        self.stopping = False

    def event(self, name, **data):
        self.send({"type": "event", "event": name, "data": data})

    def status(self, state, transport=None):
        self.radio = {"state": state, "transport": transport}
        self.event("server_status", **self.radio)

    def progress(self, state, address=None, message=None):
        self.pairing = {"state": state, "address": address, "message": message}
        self.event("pairing", **self.pairing)

    def snapshot(self):
        self.event("devices", devices=list(self.devices.values()))

    def connected(self, mote):
        if self.stopping:
            return
        board = isinstance(mote, WiiBoard)
        self.devices[mote.address] = dict(
            address=mote.address,
            name="Wii Balance Board" if board else "Wiimote",
            kind="balance_board" if board else "wiimote",
            paired=True,
            connected=True,
        )
        self.snapshot()
        if board:
            print("[balance] Connected; waiting for calibrated sensor reports", file=sys.stderr, flush=True)
            if self.pairing["state"] == "scanning" and self.operation:
                # A saved board can reconnect while inquiry is running. Use that
                # connection and stop inquiry rather than pairing a second board.
                self.operation.cancel()
                self.progress("idle", mote.address, "Board connected")
            last_sent = 0

            def reading(_data):
                nonlocal last_sent
                now = time.monotonic()
                if self.stopping or not mote.connected or now - last_sent < 1 / 30:
                    return
                if last_sent == 0:
                    print(
                        f"[balance] Publishing first JSON reading: x={mote.center_of_balance[0]} "
                        f"y={mote.center_of_balance[1]} weight_kg={mote.total_weight}",
                        file=sys.stderr, flush=True,
                    )
                last_sent = now
                self.event(
                    "balance",
                    address=mote.address,
                    x=mote.center_of_balance[0],
                    y=mote.center_of_balance[1],
                    weight_kg=mote.total_weight,
                    battery_bars=mote.battery_bars,
                )

            # Publish from actual extension reports, including unchanged readings.
            # A timer replaying cached CoB would make a dead sensor stream look fresh.
            mote.on_balance_report(reading)

    def disconnected(self, address):
        if address in self.devices:
            self.devices[address]["connected"] = False
            self.snapshot()

    async def start_radio(self, transport):
        self.server = self.server_factory(transport, "")
        self.server.on_wiimote_connected(self.connected)
        self.server.on_wiimote_disconnected(self.disconnected)
        self.server.on_error(lambda message: self.event("error", message=message))
        try:
            async with asyncio.timeout(25):
                await self.server.start()
            self.status("listening", transport)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            try:
                await self.close_radio()
            except Exception:
                traceback.print_exc(file=sys.stderr)
            self.event("error", message=f"Could not start adapter: {error}")

    async def pair_board(self, address=None):
        try:
            if address is None:
                address = (await self.server.scan())[0]
            if self.stopping:
                return
            self.progress(
                "pairing", address, "Authenticating and initialising your board…"
            )
            async with asyncio.timeout(30):
                mote = await self.server.pair(address)
            if not isinstance(mote, WiiBoard):
                await mote.disconnect()
                raise ValueError("The discovered device is not a Wii Balance Board")
            self.progress("idle", mote.address, "Board connected")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            self.progress(
                "failed",
                address,
                f"Pairing failed: {error or 'Timed out'}. Press SYNC and try again.",
            )

    async def cancel(self):
        if self.operation:
            if not self.operation.done():
                self.operation.cancel()
            with suppress(asyncio.CancelledError):
                await self.operation
        self.operation = None
        self.progress("idle")

    async def close_radio(self):
        server, self.server = self.server, None
        try:
            if server:
                async with asyncio.timeout(2):
                    await server.stop()
        finally:
            self.devices.clear()
            self.status("stopped")
            self.snapshot()

    async def stop(self):
        self.stopping = True
        try:
            try:
                await self.cancel()
            finally:
                await self.close_radio()
        finally:
            self.stopping = False

    async def command(self, command):
        command_id = command.get("id") if isinstance(command, dict) else None
        try:
            if type(command_id) is not int:
                raise ValueError("Command id must be an integer")
            method = command.get("method")
            params = command.get("params", {})
            if method not in self.capabilities or not isinstance(params, dict):
                raise ValueError("Unknown command or invalid parameters")
            busy = self.operation and not self.operation.done()
            if method == "server_start":
                if busy or self.server:
                    raise ValueError("The adapter is already active or starting")
                transport = params.get("transport")
                if not isinstance(transport, str) or not transport:
                    raise ValueError("Choose an adapter")
                self.status("starting", transport)
                self.operation = asyncio.create_task(self.start_radio(transport))
            elif method in ("wiiboard_pair", "wiimote_pair"):
                if busy or self.radio["state"] != "listening":
                    raise ValueError("Wait for the adapter or current operation")
                if any(d["connected"] for d in self.devices.values()):
                    raise ValueError("Disconnect the current board before pairing")
                address = params.get("address") if method == "wiimote_pair" else None
                if method == "wiimote_pair" and (
                    not isinstance(address, str)
                    or not re.fullmatch(
                        r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}(/P)?", address
                    )
                ):
                    raise ValueError("Invalid Bluetooth address")
                self.progress(
                    "pairing" if address else "scanning",
                    address,
                    "Press the red SYNC button on your board",
                )
                self.operation = asyncio.create_task(self.pair_board(address))
            elif method == "wiimote_cancel_pairing":
                if self.radio["state"] == "starting":
                    raise ValueError("Switch off to cancel adapter startup")
                await self.cancel()
            elif method == "server_stop":
                await self.stop()
            elif method == "server_status":
                self.event("server_status", **self.radio)
            elif method == "wiimote_list":
                self.snapshot()
            elif method == "wiimote_disconnect":
                if busy or not self.server:
                    raise ValueError("Wait for the current operation")
                mote = next(
                    (
                        s.wiimote
                        for s in self.server.wiimotes
                        if s.addr == params.get("address")
                    ),
                    None,
                )
                if mote is None:
                    raise ValueError("Device is not connected")
                async with asyncio.timeout(5):
                    await mote.disconnect()
            elif method == "adapter_list":
                self.event("adapters", adapters=await asyncio.to_thread(adapters))
            self.send({"type": "response", "id": command_id, "ok": True})
        except Exception as error:
            self.send(
                {"type": "response", "id": command_id, "ok": False, "error": str(error)}
            )


def adapters():
    result = []
    indices = {}
    with usb1.USBContext() as context:
        for device in context.getDeviceIterator():
            info = derive_device_info(device)
            base = info.transport_names[0]
            index = indices.get(base, 0)
            indices[base] = index + 1
            if info.is_bluetooth:
                transport = (
                    info.transport_names[-1]
                    if info.serial_number
                    else f"{base}#{index}"
                )
                result.append(
                    dict(
                        transport=transport,
                        name=info.product or "Bluetooth adapter",
                        manufacturer=info.manufacturer,
                        serial=info.serial_number,
                    )
                )
    return result


async def main(wire):
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    output, _ = await loop.connect_write_pipe(asyncio.Protocol, wire)

    def send(message):
        # Pipe transport writes are non-blocking. Drop only balance telemetry under
        # back-pressure; control transitions must remain ordered and observable.
        if (
            message.get("event") == "balance"
            and output.get_write_buffer_size() > 65_536
        ):
            return
        output.write((json.dumps(message, allow_nan=False) + "\n").encode())

    controller = Controller(send)
    controller.event("ready", protocol_version=1, capabilities=controller.capabilities)
    controller.status("stopped")
    controller.snapshot()
    reader = asyncio.StreamReader(limit=65_536)
    input_transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )

    async def commands():
        try:
            while line := await reader.readline():
                try:
                    command = json.loads(line)
                except ValueError as error:
                    controller.event("error", message=f"Invalid command JSON: {error}")
                    continue
                await controller.command(command)
        finally:
            stop.set()

    task = asyncio.create_task(commands())
    try:
        await stop.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            await controller.stop()
        finally:
            input_transport.close()
            output.close()
