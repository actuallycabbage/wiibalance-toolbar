# Bluetooth Transport Layer
# Exports interfaces and default Bumble implementation

from .interfaces import (
    PSM,
    L2CAPChannel,
    BluetoothConnection,
    BluetoothTransport,
)

from .bumble import (
    BumbleL2CAPChannel,
    BumbleBluetoothConnection,
    BumbleBluetoothTransport,
    create_transport,
    discover_devices,
    pair_device,
)

# Export the Bumble implementation as the default
__all__ = [
    # Interfaces
    "PSM",
    "L2CAPChannel",
    "BluetoothConnection",
    "BluetoothTransport",
    # Bumble implementation
    "BumbleL2CAPChannel",
    "BumbleBluetoothConnection",
    "BumbleBluetoothTransport",
    "create_transport",
    "discover_devices",
    "pair_device",
]
