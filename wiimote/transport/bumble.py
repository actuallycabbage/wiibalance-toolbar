# Bumble-specific Bluetooth Transport Implementation

from __future__ import annotations

import asyncio
import enum
from contextlib import asynccontextmanager
from typing import Any, Callable, NamedTuple, Optional

from bumble import core, l2cap
from bumble.device import Connection, Device, DeviceConfiguration
from bumble.hci import (
    HCI_PIN_CODE_REQUEST_NEGATIVE_REPLY_COMMAND,
    HCI_PIN_CODE_REQUEST_REPLY_COMMAND,
    Address,
    HCI_PIN_Code_Request_Reply_Command,
    Role,
)
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.transport import open_transport as bumble_open_transport

from .interfaces import PSM
from .interfaces import BluetoothConnection as BluetoothConnectionInterface
from .interfaces import ChannelPair
from .interfaces import L2CAPChannel as L2CAPChannelInterface

# ==============================================================================
# Wiimote Pairing Helpers
# ==============================================================================


def _addr_bytes(addr: str) -> bytes:
    """Convert Bluetooth address string to bytes"""
    return bytes.fromhex(addr.split("/")[0].replace(":", ""))


def _pad16(b: bytes) -> bytes:
    """Pad PIN to 16 bytes"""
    if len(b) > 16:
        raise ValueError("PIN too long")
    return b + b"\x00" * (16 - len(b))


def _pin_from_address(addr: str) -> bytes:
    """
    Generate Wiimote PIN from Bluetooth address.

    Wiimotes use their own Bluetooth address (reversed) as the PIN code.
    This is the standard pairing method for Wiimotes.

    As per the wiibrew wiki, the wiimote has two authentication schemes:
    1. Pairing: The code for temporary pairing is the binary Bluetooth address
                of the Wiimote.
    2. Bonding: The PIN code for bonding is the binary Bluetooth address of the
                host machine.

    So, if you want to avoid hitting sync all the time take the bonding route
    and send the host address.

    ref: https://wiibrew.org/wiki/Wiimote#Bluetooth_Pairing
    """
    return _addr_bytes(addr)[::-1]  # 6 bytes, reversed


# ==============================================================================
# Channel and Connection Implementations
# ==============================================================================


class BumbleL2CAPChannel(L2CAPChannelInterface):
    """Bumble implementation of L2CAP channel"""

    _channel: l2cap.ClassicChannel

    def __init__(self, channel: l2cap.ClassicChannel):
        self._channel = channel

    @property
    def sink(self) -> Optional[Callable[[bytes], Any]]:
        """Get the data sink callback"""
        return self._channel.sink

    @sink.setter
    def sink(self, callback: Optional[Callable[[bytes], Any]]) -> None:
        """Set the data sink callback for receiving data"""
        self._channel.sink = callback

    def send(self, data: bytes) -> None:
        """Send data on this L2CAP channel"""
        self._channel.send_pdu(data)

    async def disconnect(self) -> None:
        """Disconnect this L2CAP channel"""
        await self._channel.disconnect()


class BumbleChannelPair(NamedTuple):
    control: l2cap.ClassicChannel
    interrupt: l2cap.ClassicChannel

    def into_wrapped(self) -> ChannelPair:
        return ChannelPair(
            control=BumbleL2CAPChannel(self.control),
            interrupt=BumbleL2CAPChannel(self.interrupt),
        )


class ChannelOrigin(enum.Enum):
    # device initiated
    OUTBOUND = ("outbound",)

    # wiimote initiated
    INBOUND = "inbound"


class BumbleBluetoothConnection(BluetoothConnectionInterface):
    """Bumble implementation of Bluetooth connection"""

    def __init__(
        self,
        connection: Connection,
        origin: ChannelOrigin,
        channels: Optional[BumbleChannelPair] = None,
        authenticated=False,
    ):
        self._connection = connection
        self._origin = origin
        self._channels = channels
        self._authenticated = authenticated

    @staticmethod
    def from_inbound(connection: Connection, channels: BumbleChannelPair):
        return BumbleBluetoothConnection(
            connection, ChannelOrigin.INBOUND, channels, False
        )

    @staticmethod
    def from_outbound(connection: Connection):
        return BumbleBluetoothConnection(
            connection, ChannelOrigin.OUTBOUND, None, False
        )

    async def get_channels(self) -> ChannelPair:
        if self._channels is not None:
            return self._channels.into_wrapped()

        self._channels = BumbleChannelPair(
            control=await self._connection.create_l2cap_channel(
                spec=l2cap.ClassicChannelSpec(PSM.HID_CONTROL)
            ),
            interrupt=await self._connection.create_l2cap_channel(
                spec=l2cap.ClassicChannelSpec(PSM.HID_INTERRUPT)
            ),
        )

        return self._channels.into_wrapped()

    @property
    def peer_address(self) -> str:
        """Get the peer device address"""
        return str(self._connection.peer_address)

    @property
    def handle(self) -> int:
        """Get the connection handle"""
        return self._connection.handle

    # async def authenticate(self) -> None:
    #     """Authenticate the connection (if needed)"""
    #     print(f">>> Authenticating {self.peer_address}...")
    #     await self._connection.authenticate()
    #     print(f">>> Authenticated {self.peer_address}")
    #
    #     keystore = self._connection.device.keystore
    #     if keystore:
    #         filename = getattr(keystore, "filename", None)
    #         if filename:
    #             print(f">>> Bumble keystore: {filename}")
    #
    #         keys = await keystore.get(str(self._connection.peer_address))
    #         if keys and keys.link_key:
    #             print(f">>> Bumble keystore has link key for {self.peer_address}")
    #         else:
    #             print(f">>> Bumble keystore has no link key for {self.peer_address}")

    async def disconnect(self) -> None:
        """Disconnect from the peer device"""
        await self._connection.disconnect()


# TODO: reject non-wiimote requests
class AutoAcceptPairing(PairingDelegate):
    def __init__(self):
        super().__init__(PairingDelegate.NO_OUTPUT_NO_INPUT)

    async def accept(self) -> bool:
        print("pairing requested: accepting")
        return True

    async def confirm(self, auto: bool = False) -> bool:
        print(f"pairing confirmation requested: accepting auto={auto}")
        return True

    async def get_string(self, max_length: int) -> str | None:
        # Classic legacy PIN path. For Wii-family devices this may need to be
        # the Nintendo-specific address-derived PIN, not "0000".
        print(f"legacy PIN requested, max_length={max_length}")
        return None


# def on_connection(connection):
#     print(f"connected: {connection}")
#
#     connection.on(
#         connection.EVENT_CLASSIC_PAIRING,
#         lambda: print("classic pairing completed"),
#     )
#     connection.on(
#         connection.EVENT_PAIRING,
#         lambda keys: print(f"LE pairing completed: {keys}"),
#     )
#     connection.on(
#         connection.EVENT_PAIRING_FAILURE,
#         lambda reason: print(f"pairing failed: {reason}"),
#     )
#     connection.on(
#         connection.EVENT_DISCONNECTION,
#         lambda reason: print(f"disconnected: {reason}"),
#     )


class BumbleBluetoothTransport:
    """Bumble implementation of Bluetooth transport"""

    def __init__(
        self,
        transport_name: str,
        on_connect: Optional[Callable[[Connection], None]] = None,
        *,
        config_file: Optional[str] = None,
        enable_wiimote_pairing: bool = True,
    ):
        """
        Initialize Bumble Bluetooth transport.

        Args:
            config_file: Path to Bumble device configuration file
            transport_name: Bumble transport string (e.g., "usb:0" or "tcp-client:127.0.0.1:9000")
            enable_wiimote_pairing: Enable Wiimote pairing monkeypatch (intercepts PIN requests)
        """
        self.config_file: Optional[str] = config_file
        self.transport_name = transport_name
        self.enable_wiimote_pairing = enable_wiimote_pairing
        self._hci_transport = None
        self._device: Optional[Device] = None
        self._connections: dict[str, BumbleBluetoothConnection] = {}
        self._original_send_command = None
        self._original_send_command_sync = None
        self._accept_task: Optional[asyncio.Task] = None

        # for wiimote-initiated connections
        self._on_connection = on_connect

    def _install_wiimote_pairing_patch(self) -> None:
        """
        Install monkeypatch for Wiimote pairing.

        Intercepts PIN_CODE_REQUEST_NEGATIVE_REPLY commands and replaces them
        with PIN_CODE_REQUEST_REPLY using the peer's address as the PIN.
        This is required for Wiimote pairing to work.
        """
        if not self._device:
            return

        HostCls = type(self._device.host)
        self._original_send_command = HostCls.send_command
        self._original_send_command_sync = HostCls.send_command_sync

        def pin_reply_for(command):
            # Get the command opcode
            opcode = (
                getattr(command, "opcode", None)
                or getattr(command, "command_opcode", None)
                or getattr(command, "op_code", None)
            )
            name = command.__class__.__name__.lower()

            print("*** intercepted opcode", opcode, name)

            # Check if this is a PIN negative reply
            is_pin_negative_reply = (
                opcode == HCI_PIN_CODE_REQUEST_NEGATIVE_REPLY_COMMAND
                or "pin_code_request_negative_reply" in name
            )

            if not is_pin_negative_reply:
                return None

            # host address for bonding request (use wiimote address for temp pair request)
            pin6 = _pin_from_address(self.device.public_address.to_string(False))
            pin16 = _pad16(pin6)

            print(
                ">>> Wiimote pairing: Intercepted PIN request, "
                f"sending PIN {pin6.hex(' ')} for {command.bd_addr}"
            )

            print(
                "*** replaced intercepted opcode with",
                HCI_PIN_CODE_REQUEST_REPLY_COMMAND,
                "HCI_PIN_CODE_REQUEST_REPLY_COMMAND",
            )

            return HCI_PIN_Code_Request_Reply_Command(command.bd_addr, 6, pin16)

        def patched_send_command(
            self_host, command, check_result=False, response_timeout=None
        ):
            if reply := pin_reply_for(command):
                return self._original_send_command(
                    self_host, reply, check_result, response_timeout
                )

            # For all other commands, use the original method
            return self._original_send_command(
                self_host, command, check_result, response_timeout
            )

        def patched_send_command_sync(self_host, command):
            if reply := pin_reply_for(command):
                return self._original_send_command_sync(self_host, reply)

            return self._original_send_command_sync(self_host, command)

        # Install the patch
        HostCls.send_command = patched_send_command
        HostCls.send_command_sync = patched_send_command_sync

    def _uninstall_wiimote_pairing_patch(self) -> None:
        """Restore original send_command if patched"""
        if self._original_send_command and self._device:
            HostCls = type(self._device.host)
            HostCls.send_command = self._original_send_command
            if self._original_send_command_sync:
                HostCls.send_command_sync = self._original_send_command_sync
            self._original_send_command = None
            self._original_send_command_sync = None

    async def start(self) -> None:
        """Start the Bluetooth transport"""
        # Open HCI transport
        self._hci_transport = await bumble_open_transport(self.transport_name)

        # Create and configure device
        if self.config_file is not None:
            print("config file isn't none", self.config_file)
            self._device = Device.from_config_file_with_hci(
                self.config_file, self._hci_transport.source, self._hci_transport.sink
            )

        else:
            self._device = Device.from_config_with_hci(
                DeviceConfiguration(
                    name="Bumble",
                    address=Address("F0:F1:F2:F3:F4:F5"),
                    keystore="JsonKeyStore",
                ),
                self._hci_transport.source,
                self._hci_transport.sink,
            )

        # Enable Classic Bluetooth (BR/EDR)
        self._device.classic_enabled = True

        # Power on the device
        await self._device.power_on()

        if self._on_connection is not None:
            self.device.classic_accept_any = False
            self.device.pairing_config_factory = lambda _: PairingConfig(
                sc=False,
                mitm=False,
                bonding=True,
                delegate=AutoAcceptPairing(),
            )
            self._create_hid_servers()
            await self.device.set_discoverable(False)
            await self.device.set_connectable(True)
            self._accept_task = asyncio.create_task(self._accept_connections())

        keystore = self._device.keystore
        if keystore:
            filename = getattr(keystore, "filename", None)
            if filename:
                print(f">>> Bumble keystore: {filename}")

        self._device.host.on(
            "classic_pairing",
            lambda bd_addr: print(f">>> Bumble classic pairing complete for {bd_addr}"),
        )
        self._device.host.on(
            "classic_pairing_failure",
            lambda bd_addr, status: print(
                f">>> Bumble classic pairing failed for {bd_addr}: {status}"
            ),
        )
        self._device.host.on(
            "link_key",
            lambda bd_addr, link_key, key_type: print(
                f">>> Bumble link key notification for {bd_addr}: type={key_type}"
            ),
        )

        # Install Wiimote pairing patch if enabled
        if self.enable_wiimote_pairing:
            self._install_wiimote_pairing_patch()

    def _create_hid_servers(self) -> None:
        """Accept Wiimote-initiated HID L2CAP channels."""
        if not self._device:
            return

        def on_channel(channel):
            channels = getattr(channel.connection, "_wiimote_l2cap_channels", None)
            if channels is None:
                channels = {}
                setattr(channel.connection, "_wiimote_l2cap_channels", channels)

            channels[channel.psm] = channel
            print(f">>> Accepted incoming L2CAP channel PSM 0x{channel.psm:04X}")

        self._device.create_l2cap_server(
            l2cap.ClassicChannelSpec(psm=0x0011),
            handler=on_channel,
        )
        self._device.create_l2cap_server(
            l2cap.ClassicChannelSpec(psm=0x0013),
            handler=on_channel,
        )

    async def _accept_connections(self) -> None:
        """Accept incoming BR/EDR connections while requesting central role."""
        if not self._device or not self._on_connection:
            return

        while True:
            try:
                connection = await self._device.accept(role=Role.CENTRAL, timeout=None)
                setattr(connection, "_wiimote_expect_incoming_l2cap", True)
                self._on_connection(connection)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f">>> Incoming accept failed: {e}")

    async def stop(self) -> None:
        """Stop the Bluetooth transport and cleanup"""
        if self._accept_task:
            self._accept_task.cancel()
            try:
                await self._accept_task
            except asyncio.CancelledError:
                pass
            self._accept_task = None

        # Uninstall pairing patch if installed
        self._uninstall_wiimote_pairing_patch()

        # Disconnect all connections
        for conn in list(self._connections.values()):
            await conn.disconnect()
        self._connections.clear()

        # Power off device
        if self._device:
            await self._device.power_off()

        # Close HCI transport
        if self._hci_transport:
            await self._hci_transport.close()

    async def connect(self, address: str) -> BumbleBluetoothConnection:
        """
        Connect to a Bluetooth device.

        Args:
            address: Bluetooth address (e.g., "00:1A:1A:80:D7:51" or "00:1A:1A:80:D7:51/P")

        Returns:
            BluetoothConnection instance
        """
        if not self._device:
            raise RuntimeError("Transport not started")

        # Check if already connected
        if address in self._connections:
            return self._connections[address]

        # Connect to peer (BR/EDR)
        connection = await self._device.connect(
            address, transport=core.PhysicalTransport.BR_EDR
        )

        await connection.authenticate()

        # Wrap and store connection
        wrapped = BumbleBluetoothConnection(connection)
        self._connections[address] = wrapped

        return wrapped

    async def disconnect(self, address: str) -> None:
        """
        Disconnect from a Bluetooth device.

        Args:
            address: Bluetooth address
        """
        if address in self._connections:
            await self._connections[address].disconnect()
            del self._connections[address]

    def get_connection(self, address: str) -> Optional[BumbleBluetoothConnection]:
        """Get an existing connection by address"""
        return self._connections.get(address)

    @property
    def device(self) -> Device:
        """Get the underlying Bumble device"""
        if not self._device:
            raise RuntimeError("Transport not started")
        return self._device

    @property
    def address(self) -> str:
        """Get the local Bluetooth address"""
        if not self._device:
            raise RuntimeError("Transport not started")
        return str(self._device.public_address)

    def scan(self, on_found: Callable[[], None]):
        class Listener(Device.Listener):
            def on_inquiry_result(self, address, class_of_device, data, rssi):
                # todo: check
                on_found()

        pass


@asynccontextmanager
async def create_transport(
    transport_name: str,
    on_connection: Optional[Callable[[Connection], None]] = None,
    *,
    config_file: Optional[str] = None,
    enable_wiimote_pairing: bool = False,
):
    """
    Async context manager for creating a Bumble Bluetooth transport.

    Usage:
        async with create_transport("config.json", "usb:0") as transport:
            connection = await transport.connect("00:1A:1A:80:D7:51")
            # ... use connection ...

        # For Wiimote pairing:
        async with create_transport("config.json", "usb:0", enable_wiimote_pairing=True) as transport:
            connection = await transport.connect("00:1A:1A:80:D7:51")
            await connection.authenticate()  # Will use Wiimote PIN automatically

    Args:
        config_file: Path to Bumble device configuration file
        transport_name: Bumble transport string
        enable_wiimote_pairing: Enable Wiimote pairing monkeypatch (required for initial pairing)

    Yields:
        BumbleBluetoothTransport instance
    """
    transport = BumbleBluetoothTransport(
        transport_name,
        on_connection,
        config_file=config_file,
        enable_wiimote_pairing=enable_wiimote_pairing,
    )
    try:
        await transport.start()
        yield transport
    finally:
        await transport.stop()
