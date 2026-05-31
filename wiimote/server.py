# Wiimote Server

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeAlias, cast

from bumble import core, l2cap
from bumble.device import Connection, Device
from bumble.hci import Role
from bumble.l2cap import ClassicChannel, ClassicChannelServer
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.transport import open_transport
from bumble.transport.common import Transport

from wiimote.device import WiimoteAwareDevice
from wiimote.transport.bumble import BumbleBluetoothConnection, BumbleChannelPair
from wiimote.transport.interfaces import PSM
from wiimote.wiiboard import WiiBoard
from wiimote.wiimote import Wiimote

WiiDevice: TypeAlias = Wiimote | WiiBoard


@dataclass
class PendingIncomingWiimoteConnection:
    """
    Holds temporary Wiimote connection data from wiimote-initiated while waiting
    for the Wiimote to complete initialising the two L2CAP channels.
    """

    def __init__(
        self,
        connection: Connection,
        control_channel: Optional[ClassicChannel],
        interrupt_channel: Optional[ClassicChannel],
        disconnect_listener: Callable[[Any], None],
    ):
        self.connection = connection
        self.control_channel = control_channel
        self.interrupt_channel = interrupt_channel
        self.disconnect_listener = disconnect_listener

        self.connection.on(Connection.EVENT_DISCONNECTION, self.disconnect_listener)

    def ready(self) -> bool:
        return self.control_channel is not None and self.interrupt_channel is not None

    def remove_disconnect(self):
        self.connection.remove_listener(
            Connection.EVENT_DISCONNECTION, self.disconnect_listener
        )


@dataclass
class WiimoteState:
    addr: str
    wiimote: Wiimote | WiiBoard
    index: Optional[int]  # None for wiiboards // 1-indexed


class WiimoteCollection:
    wiimotes: dict[str, WiimoteState] = {}

    async def insert(self, mote: WiiDevice) -> WiimoteState:
        addr = mote.address
        index = 1
        if addr in self.wiimotes:
            # TODO: re-connect functionality.
            try:
                await self.wiimotes[addr].wiimote.disconnect()
            except Exception as e:
                print("Wiimote reconnect disconect (lol) failed", e)
            index = self.wiimotes[addr].index
            del self.wiimotes[addr]

        else:
            indicies = sorted(
                [i.index for i in self.wiimotes.values() if i.index is not None]
            )

            # next index
            for a, b in zip(indicies, indicies[1:]):
                if b != a + 1:
                    index = a + 1
                    break

        state = WiimoteState(addr=addr, wiimote=mote, index=index)

        self.wiimotes[addr] = state

        return state

    def __iter__(self):
        return iter(self.wiimotes.values())


class AutoAcceptPairing(PairingDelegate):
    def __init__(self, conn: Connection):
        super().__init__(PairingDelegate.NO_OUTPUT_NO_INPUT)

    async def accept(self) -> bool:
        print("pairing requested: accepting")
        return True

    async def confirm(self, auto: bool = False) -> bool:
        print(f"pairing confirmation requested: accepting auto={auto}")
        return True

    async def get_string(self, max_length: int) -> str | None:
        print(f"legacy PIN requested, max_length={max_length}")
        return None


class WiimoteServer:
    def __init__(self, transport_name: str, transport_config_file: str):

        # transport
        self._transport_aexit: AsyncExitStack
        self._transport_name = transport_name
        self._transport_config_file = transport_config_file
        self._transport: Optional[Transport] = None

        # device
        self._device: Optional[Device] = None

        # l2cap servers
        self._l2cap_control_server: Optional[ClassicChannelServer] = None
        self._l2cap_interrupt_server: Optional[ClassicChannelServer] = None

        self._accept_task: Optional[asyncio.Task] = None

        # connections
        self._connections: list[Wiimote] = []

        # bd address to conn setup while polling for incomming connections
        self._incoming: dict[str, PendingIncomingWiimoteConnection] = {}

        self._wiimotes = WiimoteCollection()

        # callbacks
        self._on_wiimote_connected_callback: Optional[Callable[[WiiDevice], None]] = (
            None
        )

    def on_wiimote_connected(self, callback: Callable[[Wiimote | WiiBoard], None]):
        """Register a callback for on wiimote connection"""
        self._on_wiimote_connected_callback = callback

    @property
    def wiimotes(self):
        return self._wiimotes

    async def _start_transport(self):
        self._transport_aexit = AsyncExitStack()
        self._transport = await self._transport_aexit.enter_async_context(
            await open_transport(self._transport_name)
        )

        self._device = cast(
            WiimoteAwareDevice,
            WiimoteAwareDevice.from_config_file_with_hci(
                self._transport_config_file,
                self._transport.source,
                self._transport.sink,
            ),
        )

        self._device.classic_enabled = True
        self._device.le_enabled = False

        if snoop := os.environ.get("BUMBLE_SNOOPER"):
            from bumble.snoop import create_snooper

            self._device.host.snooper = self._transport_aexit.enter_context(
                create_snooper(snoop)
            )

        # installs the monkeypatch
        self._device.enable_wiimote_pairing()

        self._device.pairing_config_factory = lambda conn: PairingConfig(
            sc=False,
            mitm=False,
            bonding=True,
            delegate=AutoAcceptPairing(conn),
        )

        self._l2cap_control_server = self._device.create_l2cap_server(
            l2cap.ClassicChannelSpec(psm=PSM.HID_CONTROL),
            handler=self._on_l2cap_channel,
        )

        self._l2cap_interrupt_server = self._device.create_l2cap_server(
            l2cap.ClassicChannelSpec(psm=PSM.HID_INTERRUPT),
            handler=self._on_l2cap_channel,
        )

        await self._device.power_on()
        await self._device.set_connectable(True)
        await self._device.set_discoverable(True)

    def _start_accept_loop(self):
        """Accept any incoming connection requests from bonded wiimotes"""
        if self._accept_task is not None:
            return

        self._accept_task = asyncio.create_task(self._accept_loop())

    async def _accept_loop(self):
        while True:
            if self._device is None:
                return
            conn = await self._device.accept(role=Role.CENTRAL)

            self._on_inbound_connection(conn)

    async def _stop_accept_loop(self):
        if self._accept_task is None:
            return

        self._accept_task.cancel()
        try:
            await self._accept_task
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    def _on_l2cap_channel(self, chan: ClassicChannel):
        """Wiimote-initiated L2CAP channel has been established"""
        addr = chan.connection.peer_address.to_string()
        if not addr in self._incoming:
            raise RuntimeError(f"l2cap channel established before connection")

        incoming = self._incoming[addr]

        match chan.psm:
            case PSM.HID_INTERRUPT:
                incoming.interrupt_channel = chan
            case PSM.HID_CONTROL:
                incoming.control_channel = chan
            case _:
                raise RuntimeError(f"unexpected PSM channel {chan.psm}")

        if incoming.ready():
            # create that channel class, finish auth etc, we're done here.
            asyncio.create_task(self._initialise_mote_from_inbound(incoming))

    async def _initialise_mote_from_inbound(
        self, conn: PendingIncomingWiimoteConnection
    ):
        print(">>> initialising from inbound")
        if not self._device:
            raise RuntimeError("missing device")

        if not conn.ready() or not conn.control_channel or not conn.interrupt_channel:
            raise RuntimeError("initialising mote before connection is ready")

        bumbleConn = BumbleBluetoothConnection.from_inbound(
            conn.connection,
            BumbleChannelPair(
                control=conn.control_channel, interrupt=conn.interrupt_channel
            ),
        )

        print(">>> requesting name")
        name = await self._device.request_remote_name(conn.connection)
        match name:
            case "Nintendo RVL-CNT-01":
                mote = Wiimote(bumbleConn)
            case "Nintendo RVL-CNT-01-TR":
                mote = Wiimote(bumbleConn)
            case "Nintendo RVL-WBC-01":
                mote = WiiBoard(bumbleConn)
            case _:
                raise RuntimeError(f"unexpected device type: {name}")

        # drop from inbound list
        conn.remove_disconnect()
        del self._incoming[conn.connection.peer_address.to_string()]

        print(">>> connecting mote")
        await mote.connect()
        state = await self._wiimotes.insert(mote)
        print(">>> initialising mote")
        await mote.initialize(state.index or 1)

        # fire callback
        if self._on_wiimote_connected_callback:
            self._on_wiimote_connected_callback(mote)

    async def _initialise_mote_from_outbound(self, conn: Connection):
        print(">>> initialising from inbound")
        if not self._device:
            raise RuntimeError("missing device")

        bumbleConn = BumbleBluetoothConnection.from_outbound(
            conn,
        )

        print(">>> requesting name")
        name = await self._device.request_remote_name(conn)
        match name:
            case "Nintendo RVL-CNT-01":
                mote = Wiimote(bumbleConn)
            case "Nintendo RVL-CNT-01-TR":
                mote = Wiimote(bumbleConn)
            case "Nintendo RVL-WBC-01":
                mote = WiiBoard(bumbleConn)
            case _:
                raise RuntimeError(f"unexpected device type: {name}")

        await mote.connect()
        state = await self._wiimotes.insert(mote)
        await mote.initialize(state.index or 1)

        # fire callback
        if self._on_wiimote_connected_callback:
            self._on_wiimote_connected_callback(mote)

        return mote

    def _on_inbound_connection(self, conn: Connection):
        addr = conn.peer_address.to_string()

        def on_disconnect(_):
            del self._incoming[addr]

        self._incoming[addr] = PendingIncomingWiimoteConnection(
            connection=conn,
            control_channel=None,
            interrupt_channel=None,
            disconnect_listener=on_disconnect,
        )

        # authenticate the mote.
        asyncio.create_task(conn.authenticate())

    async def start(self, listen=True):
        await self._start_transport()

        if listen:
            self._start_accept_loop()

    async def stop(self):
        # stop accepting new inbound connections
        if self._accept_task is not None:
            await self._stop_accept_loop()

        # close existing connections
        for wiimote in self._wiimotes:
            try:
                await wiimote.wiimote.disconnect()
            except Exception as e:
                print("disconnect fail", e)

        # power off device
        if self._device is not None:
            await self._device.power_off()

        # close transport
        await self._transport_aexit.aclose()

    async def pair(self, address: str) -> Optional[Wiimote | WiiBoard]:
        if not self._device:
            raise RuntimeError("Transport not started")

        try:
            connection = await self._device.connect(
                address, transport=core.PhysicalTransport.BR_EDR, timeout=0x4000
            )

            await connection.authenticate()
        except Exception as e:
            return None

        return await self._initialise_mote_from_outbound(connection)

    def _on_wiimote_connection(self, conn: Connection):
        """Outbound connection to prospective Wiimote has been established"""
        pass


async def main():
    def on_connect(mote: Wiimote | WiiBoard):
        mote.on_button_change(lambda btn: print(btn.get_pressed()))

    server = WiimoteServer("usb:0", "device-config.json")
    server.on_wiimote_connected(on_connect)
    await server.start(listen=True)

    # server._transport.read

    # board: 00:25:A0:41:39:0A/P
    # mote: 00:1A:1A:80:D7:51/P
    # print('pear', await server.pair("00:25:A0:41:39:0A/P"))
    # await server.pair("00:1A:1A:80:D7:51/P")

    try:
        await asyncio.get_running_loop().create_future()  # run forever
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
