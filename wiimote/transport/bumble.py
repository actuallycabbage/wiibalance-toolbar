# Bumble-specific Bluetooth Transport Implementation

from __future__ import annotations

from typing import Optional, Callable, Any
from contextlib import asynccontextmanager

from bumble import core, l2cap
from bumble.device import Device, Connection
from bumble.transport import open_transport as bumble_open_transport
from bumble.hci import (
    HCI_PIN_CODE_REQUEST_NEGATIVE_REPLY_COMMAND,
    HCI_PIN_Code_Request_Reply_Command,
)

from .interfaces import (
    L2CAPChannel as L2CAPChannelInterface,
    BluetoothConnection as BluetoothConnectionInterface,
    BluetoothTransport as BluetoothTransportInterface,
)


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
    """
    return _addr_bytes(addr)[::-1]  # 6 bytes, reversed


# ==============================================================================
# Channel and Connection Implementations
# ==============================================================================

class BumbleL2CAPChannel(L2CAPChannelInterface):
    """Bumble implementation of L2CAP channel"""

    def __init__(self, channel):
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


class BumbleBluetoothConnection(BluetoothConnectionInterface):
    """Bumble implementation of Bluetooth connection"""

    def __init__(self, connection: Connection):
        self._connection = connection
        self._channels: dict[int, BumbleL2CAPChannel] = {}

    @property
    def peer_address(self) -> str:
        """Get the peer device address"""
        return str(self._connection.peer_address)

    @property
    def handle(self) -> int:
        """Get the connection handle"""
        return self._connection.handle

    async def create_l2cap_channel(self, psm: int) -> BumbleL2CAPChannel:
        """
        Create an L2CAP channel to the peer device.

        Args:
            psm: Protocol/Service Multiplexer (e.g., PSM.HID_CONTROL)

        Returns:
            L2CAPChannel instance
        """
        spec = l2cap.ClassicChannelSpec(psm=psm)
        channel = await self._connection.create_l2cap_channel(spec=spec)
        wrapped = BumbleL2CAPChannel(channel)
        self._channels[psm] = wrapped
        return wrapped

    def get_channel(self, psm: int) -> Optional[BumbleL2CAPChannel]:
        """Get an existing L2CAP channel by PSM"""
        return self._channels.get(psm)

    async def authenticate(self) -> None:
        """Authenticate the connection (if needed)"""
        await self._connection.authenticate()

    async def disconnect(self) -> None:
        """Disconnect from the peer device"""
        # Close all channels first
        for channel in self._channels.values():
            await channel.disconnect()
        self._channels.clear()

        await self._connection.disconnect()


class BumbleBluetoothTransport(BluetoothTransportInterface):
    """Bumble implementation of Bluetooth transport"""

    def __init__(self, config_file: str, transport_name: str, *,
                 enable_wiimote_pairing: bool = False):
        """
        Initialize Bumble Bluetooth transport.

        Args:
            config_file: Path to Bumble device configuration file
            transport_name: Bumble transport string (e.g., "usb:0" or "tcp-client:127.0.0.1:9000")
            enable_wiimote_pairing: Enable Wiimote pairing monkeypatch (intercepts PIN requests)
        """
        self.config_file = config_file
        self.transport_name = transport_name
        self.enable_wiimote_pairing = enable_wiimote_pairing
        self._hci_transport = None
        self._device: Optional[Device] = None
        self._connections: dict[str, BumbleBluetoothConnection] = {}
        self._original_send_command = None

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

        def patched_send_command(self_host, command, check_result=False, response_timeout=None):
            # Get the command opcode
            opcode = (
                getattr(command, "opcode", None)
                or getattr(command, "command_opcode", None)
                or getattr(command, "op_code", None)
            )
            name = command.__class__.__name__.lower()

            # Check if this is a PIN negative reply
            is_pin_negative_reply = (
                opcode == HCI_PIN_CODE_REQUEST_NEGATIVE_REPLY_COMMAND
                or "pin_code_request_negative_reply" in name
            )

            if is_pin_negative_reply:
                # Extract peer address
                peer_addr = str(command.bd_addr)

                # Generate PIN from peer address (Wiimote standard)
                pin6 = _pin_from_address(peer_addr)
                pin16 = _pad16(pin6)

                # Create PIN reply command
                reply = HCI_PIN_Code_Request_Reply_Command(command.bd_addr, 6, pin16)

                print(f">>> Wiimote pairing: Intercepted PIN request, sending PIN {pin6.hex(' ')} for {peer_addr}")

                # Send the modified command using the original method
                return self._original_send_command(self_host, reply, check_result, response_timeout)

            # For all other commands, use the original method
            return self._original_send_command(self_host, command, check_result, response_timeout)

        # Install the patch
        HostCls.send_command = patched_send_command

    def _uninstall_wiimote_pairing_patch(self) -> None:
        """Restore original send_command if patched"""
        if self._original_send_command and self._device:
            HostCls = type(self._device.host)
            HostCls.send_command = self._original_send_command
            self._original_send_command = None

    async def start(self) -> None:
        """Start the Bluetooth transport"""
        # Open HCI transport
        self._hci_transport = await bumble_open_transport(self.transport_name)

        # Create and configure device
        self._device = Device.from_config_file_with_hci(
            self.config_file,
            self._hci_transport.source,
            self._hci_transport.sink
        )

        # Enable Classic Bluetooth (BR/EDR)
        self._device.classic_enabled = True

        # Power on the device
        await self._device.power_on()

        # Install Wiimote pairing patch if enabled
        if self.enable_wiimote_pairing:
            self._install_wiimote_pairing_patch()

    async def stop(self) -> None:
        """Stop the Bluetooth transport and cleanup"""
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
            address,
            transport=core.PhysicalTransport.BR_EDR
        )

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


@asynccontextmanager
async def create_transport(config_file: str, transport_name: str, *,
                           enable_wiimote_pairing: bool = False):
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
    transport = BumbleBluetoothTransport(config_file, transport_name,
                                         enable_wiimote_pairing=enable_wiimote_pairing)
    try:
        await transport.start()
        yield transport
    finally:
        await transport.stop()


# Convenience functions

async def discover_devices(transport: BumbleBluetoothTransport, duration: float = 10.0) -> list[dict]:
    """
    Discover nearby Bluetooth devices.

    Args:
        transport: Active Bluetooth transport
        duration: How long to scan in seconds

    Returns:
        List of discovered devices with 'address', 'name', 'rssi' keys
    """
    # TODO: Implement device discovery using Bumble's inquiry/scan
    raise NotImplementedError("Device discovery not yet implemented")


async def pair_device(transport: BumbleBluetoothTransport, address: str, pin: Optional[str] = None) -> bool:
    """
    Pair with a Bluetooth device.

    Args:
        transport: Active Bluetooth transport
        address: Device address to pair with
        pin: Optional PIN code for pairing

    Returns:
        True if pairing successful, False otherwise
    """
    # TODO: Implement pairing using Bumble's pairing mechanisms
    raise NotImplementedError("Pairing not yet implemented")
