# Wiimote Module
# Self-contained module for Wii Balance Board and Wiimote devices

from .reports import Buttons
from .wiimote import (
    Wiimote,
    WiimoteState,
    ReportMode,
)
from .wiiboard import (
    WiiBoard,
    BalanceBoardState,
    CalibrationData,
)
from .transport import (
    create_transport,
    discover_devices,
    pair_device,
    BluetoothConnection,
    L2CAPChannel,
    BluetoothTransport,
)

__all__ = [
    "Buttons",
    "Wiimote",
    "WiimoteState",
    "ReportMode",
    "WiiBoard",
    "BalanceBoardState",
    "CalibrationData",
    # Transport layer
    "create_transport",
    "discover_devices",
    "pair_device",
    "BluetoothConnection",
    "L2CAPChannel",
    "BluetoothTransport",
]
