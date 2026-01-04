# Bluetooth Transport Interfaces
# Abstract interfaces for Bluetooth operations

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Callable, Any


# L2CAP PSM constants for common services
class PSM:
    """Common L2CAP Protocol/Service Multiplexer values"""
    SDP = 0x0001
    RFCOMM = 0x0003
    HID_CONTROL = 0x0011
    HID_INTERRUPT = 0x0013


class L2CAPChannel(ABC):
    """Abstract interface for L2CAP channel operations"""

    @property
    @abstractmethod
    def sink(self) -> Optional[Callable[[bytes], Any]]:
        """Get the data sink callback"""
        pass

    @sink.setter
    @abstractmethod
    def sink(self, callback: Optional[Callable[[bytes], Any]]) -> None:
        """Set the data sink callback for receiving data"""
        pass

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Send data on this L2CAP channel"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect this L2CAP channel"""
        pass


class BluetoothConnection(ABC):
    """Abstract interface for a Bluetooth connection to a peer device"""

    @property
    @abstractmethod
    def peer_address(self) -> str:
        """Get the peer device address"""
        pass

    @property
    @abstractmethod
    def handle(self) -> int:
        """Get the connection handle"""
        pass

    @abstractmethod
    async def create_l2cap_channel(self, psm: int) -> L2CAPChannel:
        """
        Create an L2CAP channel to the peer device.

        Args:
            psm: Protocol/Service Multiplexer (e.g., PSM.HID_CONTROL)

        Returns:
            L2CAPChannel instance
        """
        pass

    @abstractmethod
    def get_channel(self, psm: int) -> Optional[L2CAPChannel]:
        """Get an existing L2CAP channel by PSM"""
        pass

    @abstractmethod
    async def authenticate(self) -> None:
        """Authenticate the connection (if needed)"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the peer device"""
        pass


class BluetoothTransport(ABC):
    """Abstract interface for Bluetooth transport operations"""

    @abstractmethod
    async def start(self) -> None:
        """Start the Bluetooth transport"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the Bluetooth transport and cleanup"""
        pass

    @abstractmethod
    async def connect(self, address: str) -> BluetoothConnection:
        """
        Connect to a Bluetooth device.

        Args:
            address: Bluetooth address (e.g., "00:1A:1A:80:D7:51")

        Returns:
            BluetoothConnection instance
        """
        pass

    @abstractmethod
    async def disconnect(self, address: str) -> None:
        """
        Disconnect from a Bluetooth device.

        Args:
            address: Bluetooth address
        """
        pass

    @abstractmethod
    def get_connection(self, address: str) -> Optional[BluetoothConnection]:
        """Get an existing connection by address"""
        pass

    @property
    @abstractmethod
    def address(self) -> str:
        """Get the local Bluetooth address"""
        pass

    async def __aenter__(self):
        """Async context manager entry"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.stop()
