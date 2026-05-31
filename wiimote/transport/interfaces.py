# Bluetooth Transport Interfaces
# Abstract interfaces for Bluetooth operations

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from typing import Any, Callable, NamedTuple, Optional


# L2CAP PSM constants for common services
class PSM(enum.IntEnum):
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


class ChannelPair(NamedTuple):
    control: L2CAPChannel
    interrupt: L2CAPChannel


class BluetoothConnection(ABC):
    """Abstract interface for a Bluetooth connection to a peer device"""

    @abstractmethod
    async def get_channels(self) -> ChannelPair:
        """Get (or initialise) the two L2CAP wiimote channels"""
        pass

    @property
    @abstractmethod
    def peer_address(self) -> str:
        """Get the peer device address"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the peer device"""
        pass
