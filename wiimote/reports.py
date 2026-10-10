# Wiimote Protocol Implementation
# https://wiibrew.org/wiki/Wiimote

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntFlag
from typing import Optional

# ==============================================================================
# Constants
# ==============================================================================

# HID headers
HID_INPUT = 0xA1
HID_OUTPUT = 0xA2

# Input Report IDs
WM_RPT_STATUS = 0x20
WM_RPT_READ_MEMORY = 0x21
WM_RPT_ACK = 0x22
WM_RPT_BTN = 0x30
WM_RPT_BTN_ACC = 0x31
WM_RPT_BTN_EXT8 = 0x32
WM_RPT_BTN_ACC_IR12 = 0x33
WM_RPT_BTN_EXT19 = 0x34
WM_RPT_BTN_ACC_EXT16 = 0x35
WM_RPT_BTN_IR10_EXT9 = 0x36
WM_RPT_BTN_ACC_IR10_EXT6 = 0x37
WM_RPT_EXT21 = 0x3D
WM_RPT_BTN_ACC_IR36_1 = 0x3E
WM_RPT_BTN_ACC_IR36_2 = 0x3F

# Output Command IDs
WM_CMD_RUMBLE = 0x10
WM_CMD_LEDS = 0x11
WM_CMD_REPORT_MODE = 0x12
WM_CMD_IR_ENABLE = 0x13
WM_CMD_SPEAKER_ENABLE = 0x14
WM_CMD_STATUS_REQUEST = 0x15
WM_CMD_WRITE_MEMORY = 0x16
WM_CMD_READ_MEMORY = 0x17
WM_CMD_SPEAKER_DATA = 0x18
WM_CMD_SPEAKER_MUTE = 0x19
WM_CMD_IR_ENABLE_2 = 0x1A


# ==============================================================================
# Utilities
# ==============================================================================


def u16be(b: bytes, off: int = 0) -> int:
    """Read big-endian 16-bit unsigned int"""
    return struct.unpack_from(">H", b, off)[0]


def _apply_rumble(byte: int, rumble: bool) -> int:
    """Apply rumble bit (bit 0) to a command byte"""
    return (byte | 0x01) if rumble else (byte & 0xFE)


# ==============================================================================
# Button Flags
# ==============================================================================


class Buttons(IntFlag):
    """Wiimote button flags"""

    TWO = 0x0001
    ONE = 0x0002
    B = 0x0004
    A = 0x0008
    MINUS = 0x0010
    HOME = 0x0080
    LEFT = 0x0100
    RIGHT = 0x0200
    DOWN = 0x0400
    UP = 0x0800
    PLUS = 0x1000

    # Helper methods for checking button states

    def is_pressed(self, button: "Buttons") -> bool:
        """Check if a specific button is pressed"""
        return bool(self & button)

    @property
    def is_a(self) -> bool:
        """Check if A button is pressed"""
        return bool(self & Buttons.A)

    @property
    def is_b(self) -> bool:
        """Check if B button is pressed"""
        return bool(self & Buttons.B)

    @property
    def is_one(self) -> bool:
        """Check if 1 button is pressed"""
        return bool(self & Buttons.ONE)

    @property
    def is_two(self) -> bool:
        """Check if 2 button is pressed"""
        return bool(self & Buttons.TWO)

    @property
    def is_plus(self) -> bool:
        """Check if + button is pressed"""
        return bool(self & Buttons.PLUS)

    @property
    def is_minus(self) -> bool:
        """Check if - button is pressed"""
        return bool(self & Buttons.MINUS)

    @property
    def is_home(self) -> bool:
        """Check if HOME button is pressed"""
        return bool(self & Buttons.HOME)

    @property
    def is_up(self) -> bool:
        """Check if D-Pad UP is pressed"""
        return bool(self & Buttons.UP)

    @property
    def is_down(self) -> bool:
        """Check if D-Pad DOWN is pressed"""
        return bool(self & Buttons.DOWN)

    @property
    def is_left(self) -> bool:
        """Check if D-Pad LEFT is pressed"""
        return bool(self & Buttons.LEFT)

    @property
    def is_right(self) -> bool:
        """Check if D-Pad RIGHT is pressed"""
        return bool(self & Buttons.RIGHT)

    def get_pressed(self) -> set["Buttons"]:
        """
        Get a set of all currently pressed buttons.

        Returns:
            Set of Buttons enum members that are pressed
        """
        pressed = set()
        for button in Buttons:
            if self & button:
                pressed.add(button)
        return pressed

    def any_pressed(self) -> bool:
        """Check if any buttons are pressed"""
        return bool(self)

    def none_pressed(self) -> bool:
        """Check if no buttons are pressed"""
        return not bool(self)


# ==============================================================================
# Input Report Dataclasses
# ==============================================================================


@dataclass(frozen=True)
class StatusReport:
    """Report 0x20 - Status Information"""

    buttons: Buttons
    battery_low: bool
    extension_connected: bool
    speaker_enabled: bool
    ir_enabled: bool
    led1: bool
    led2: bool
    led3: bool
    led4: bool
    battery_level: int  # 0-255


@dataclass(frozen=True)
class ReadMemoryReport:
    """Report 0x21 - Read Memory Data"""

    buttons: Buttons
    size: int  # Size of data (1-16)
    error: bool
    address: int
    data: bytes


@dataclass(frozen=True)
class AckReport:
    """Report 0x22 - Acknowledge Output Report"""

    buttons: Buttons
    report_id: int  # The output report being acknowledged
    error_code: int  # 0=success, 3=error, 4/5/8=unknown


@dataclass(frozen=True)
class ButtonReport:
    """Report 0x30 - Core Buttons Only"""

    buttons: Buttons


@dataclass(frozen=True)
class ButtonAccelReport:
    """Report 0x31 - Buttons + Accelerometer"""

    buttons: Buttons
    accel_x: int
    accel_y: int
    accel_z: int


@dataclass(frozen=True)
class ButtonExt8Report:
    """Report 0x32 - Buttons + 8 Extension bytes"""

    buttons: Buttons
    extension: bytes


@dataclass(frozen=True)
class ButtonAccelIR12Report:
    """Report 0x33 - Buttons + Accelerometer + 12 IR bytes"""

    buttons: Buttons
    accel_x: int
    accel_y: int
    accel_z: int
    ir_data: bytes


@dataclass(frozen=True)
class ButtonExt19Report:
    """Report 0x34 - Buttons + 19 Extension bytes"""

    buttons: Buttons
    extension: bytes


@dataclass(frozen=True)
class ButtonAccelExt16Report:
    """Report 0x35 - Buttons + Accelerometer + 16 Extension bytes"""

    buttons: Buttons
    accel_x: int
    accel_y: int
    accel_z: int
    extension: bytes


@dataclass(frozen=True)
class ButtonIR10Ext9Report:
    """Report 0x36 - Buttons + 10 IR bytes + 9 Extension bytes"""

    buttons: Buttons
    ir_data: bytes
    extension: bytes


@dataclass(frozen=True)
class ButtonAccelIR10Ext6Report:
    """Report 0x37 - Buttons + Accelerometer + 10 IR bytes + 6 Extension bytes"""

    buttons: Buttons
    accel_x: int
    accel_y: int
    accel_z: int
    ir_data: bytes
    extension: bytes


@dataclass(frozen=True)
class Ext21Report:
    """Report 0x3D - Extension Only (21 bytes)"""

    extension: bytes


@dataclass(frozen=True)
class ButtonAccelIR36Report:
    """Report 0x3E/0x3F - Buttons + Accelerometer + IR (interleaved, 36 bytes total)"""

    buttons: Buttons
    accel_x: int
    accel_y: int
    accel_z: int
    ir_data: bytes
    part: int  # 1 or 2


# Type alias for any input report
WiimoteReport = (
    StatusReport
    | ReadMemoryReport
    | AckReport
    | ButtonReport
    | ButtonAccelReport
    | ButtonExt8Report
    | ButtonAccelIR12Report
    | ButtonExt19Report
    | ButtonAccelExt16Report
    | ButtonIR10Ext9Report
    | ButtonAccelIR10Ext6Report
    | Ext21Report
    | ButtonAccelIR36Report
)


# ==============================================================================
# Output Command Encoders
# ==============================================================================


def rumble(enable: bool) -> bytes:
    """
    Encode 0x10 - Rumble control command.

    Args:
        enable: True to turn rumble on, False to turn off

    Returns:
        Encoded command: (a2) 10 RR
    """
    return bytes([HID_OUTPUT, WM_CMD_RUMBLE, 0x01 if enable else 0x00])


def set_leds(
    led1: bool = False,
    led2: bool = False,
    led3: bool = False,
    led4: bool = False,
    *,
    rumble: bool = False,
) -> bytes:
    """
    Encode 0x11 - Player LED control command.

    Args:
        led1: LED 1 state
        led2: LED 2 state
        led3: LED 3 state
        led4: LED 4 state
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 11 LL
    """
    leds = 0
    if led1:
        leds |= 0x10
    if led2:
        leds |= 0x20
    if led3:
        leds |= 0x40
    if led4:
        leds |= 0x80

    leds = _apply_rumble(leds, rumble)
    return bytes([HID_OUTPUT, WM_CMD_LEDS, leds])


def set_report_mode(
    mode: int, *, continuous: bool = True, rumble: bool = False
) -> bytes:
    """
    Encode 0x12 - Data reporting mode command.

    Args:
        mode: Report mode ID (0x30-0x3f)
        continuous: True for continuous reporting, False for on-change only
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 12 TT MM
    """
    flags = 0x04 if continuous else 0x00
    flags = _apply_rumble(flags, rumble)
    return bytes([HID_OUTPUT, WM_CMD_REPORT_MODE, flags, mode])


def ir_enable(enable: bool = True, *, rumble: bool = False) -> bytes:
    """
    Encode 0x13 - IR camera enable command.

    Args:
        enable: True to enable IR camera, False to disable
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 13 04/00
    """
    flags = 0x04 if enable else 0x00
    flags = _apply_rumble(flags, rumble)
    return bytes([HID_OUTPUT, WM_CMD_IR_ENABLE, flags])


def speaker_enable(enable: bool = True, *, rumble: bool = False) -> bytes:
    """
    Encode 0x14 - Speaker enable command.

    Args:
        enable: True to enable speaker, False to disable
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 14 04/00
    """
    flags = 0x04 if enable else 0x00
    flags = _apply_rumble(flags, rumble)
    return bytes([HID_OUTPUT, WM_CMD_SPEAKER_ENABLE, flags])


def status_request(*, rumble: bool = False) -> bytes:
    """
    Encode 0x15 - Status information request.

    Wiimote will respond with a 0x20 status report containing:
    - Battery level
    - LED states
    - Extension connection status

    Args:
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 15 00
    """
    flags = _apply_rumble(0x00, rumble)
    return bytes([HID_OUTPUT, WM_CMD_STATUS_REQUEST, flags])


def write_memory(
    address: int, data: bytes, *, register_space: bool = False, rumble: bool = False
) -> bytes:
    """
    Encode 0x16 - Write memory/register command.

    Args:
        address: Memory address (24-bit, big-endian)
        data: Data to write (1-16 bytes)
        register_space: True for register space (0x04), False for EEPROM
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 16 MM AA AA AA SS DD...

    Raises:
        ValueError: If data length exceeds 16 bytes
    """
    if len(data) > 16:
        raise ValueError("Data length must be <= 16 bytes")

    flags = 0x04 if register_space else 0x00
    flags = _apply_rumble(flags, rumble)

    # Pack 24-bit address as big-endian
    addr_bytes = struct.pack(">I", address)[1:]  # Take last 3 bytes
    size = len(data)

    # Report 0x16 requires a fixed 16-byte data field, even for a one-byte write.
    # The size byte is the actual write length; padding is not written to registers.
    return (
        bytes([HID_OUTPUT, WM_CMD_WRITE_MEMORY, flags])
        + addr_bytes
        + bytes([size])
        + data.ljust(16, b"\x00")
    )


def read_memory(
    address: int, size: int, *, register_space: bool = False, rumble: bool = False
) -> bytes:
    """
    Encode 0x17 - Read memory/register command.

    Wiimote will respond with a 0x21 read memory report.

    Args:
        address: Memory address (24-bit, big-endian)
        size: Number of bytes to read (1-65535)
        register_space: True for register space (0x04), False for EEPROM
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 17 MM AA AA AA SS SS
    """
    flags = 0x04 if register_space else 0x00
    flags = _apply_rumble(flags, rumble)

    # Pack 24-bit address as big-endian
    addr_bytes = struct.pack(">I", address)[1:]  # Take last 3 bytes
    size_bytes = struct.pack(">H", size)

    return bytes([HID_OUTPUT, WM_CMD_READ_MEMORY, flags]) + addr_bytes + size_bytes


def speaker_data(data: bytes, *, rumble: bool = False) -> bytes:
    """
    Encode 0x18 - Speaker data command.

    Args:
        data: Audio samples (1-20 bytes)
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 18 LL DD...

    Raises:
        ValueError: If data length exceeds 20 bytes
    """
    if len(data) > 20:
        raise ValueError("Speaker data must be <= 20 bytes")

    # Length is stored shifted left by 3 bits
    length = len(data) << 3
    length = _apply_rumble(length, rumble)

    # Pad to 20 bytes
    padded_data = data + bytes(20 - len(data))

    return bytes([HID_OUTPUT, WM_CMD_SPEAKER_DATA, length]) + padded_data


def speaker_mute(mute: bool = True, *, rumble: bool = False) -> bytes:
    """
    Encode 0x19 - Speaker mute command.

    Args:
        mute: True to mute, False to unmute
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 19 MM
    """
    flags = 0x04 if mute else 0x00
    flags = _apply_rumble(flags, rumble)
    return bytes([HID_OUTPUT, WM_CMD_SPEAKER_MUTE, flags])


def ir_enable_2(enable: bool = True, *, rumble: bool = False) -> bytes:
    """
    Encode 0x1A - IR camera enable 2 command.

    This is the second IR initialization step (pulls pin 4 low).
    Must be called after ir_enable().

    Args:
        enable: True to enable IR, False to disable
        rumble: Enable rumble during this command

    Returns:
        Encoded command: (a2) 1a 04/00
    """
    flags = 0x04 if enable else 0x00
    flags = _apply_rumble(flags, rumble)
    return bytes([HID_OUTPUT, WM_CMD_IR_ENABLE_2, flags])


def set_player_leds(player: int, *, rumble: bool = False) -> bytes:
    """
    Set LEDs to indicate player number (1-4).

    Args:
        player: Player number (1-4)
        rumble: Enable rumble during this command

    Returns:
        Encoded LED command

    Raises:
        ValueError: If player not in range 1-4
    """
    if not 1 <= player <= 4:
        raise ValueError("Player must be 1-4")

    leds = [False, False, False, False]
    leds[player - 1] = True
    return set_leds(leds[0], leds[1], leds[2], leds[3], rumble=rumble)


def initialize_ir_camera() -> list[bytes]:
    """
    Get the command sequence to initialize the IR camera.

    Returns:
        List of commands to send in sequence
    """
    return [ir_enable(True), ir_enable_2(True)]


# ==============================================================================
# Input Report Parsers
# ==============================================================================


def parse_status_report(pkt: bytes) -> Optional[StatusReport]:
    """Parse 0x20 status report: (a1) 20 BB BB LF 00 00 VV"""
    if len(pkt) < 8 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_STATUS:
        return None

    buttons = Buttons(u16be(pkt, 2))
    lf = pkt[4]

    return StatusReport(
        buttons=buttons,
        battery_low=bool(lf & 0x01),
        extension_connected=bool(lf & 0x02),
        speaker_enabled=bool(lf & 0x04),
        ir_enabled=bool(lf & 0x08),
        led1=bool(lf & 0x10),
        led2=bool(lf & 0x20),
        led3=bool(lf & 0x40),
        led4=bool(lf & 0x80),
        battery_level=pkt[7],
    )


def parse_read_memory_report(pkt: bytes) -> Optional[ReadMemoryReport]:
    """Parse 0x21 read memory report: (a1) 21 BB BB SE AA AA DD...DD"""
    if len(pkt) < 8 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_READ_MEMORY:
        return None

    buttons = Buttons(u16be(pkt, 2))
    se = pkt[4]
    size = ((se >> 4) & 0x0F) + 1
    error = bool(se & 0x0F)
    address = u16be(pkt, 5)
    data = pkt[7 : 7 + size] if not error else b""

    return ReadMemoryReport(
        buttons=buttons, size=size, error=error, address=address, data=data
    )


def parse_ack_report(pkt: bytes) -> Optional[AckReport]:
    """Parse 0x22 acknowledgment report: (a1) 22 BB BB RR EE"""
    if len(pkt) < 6 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_ACK:
        return None

    return AckReport(
        buttons=Buttons(u16be(pkt, 2)), report_id=pkt[4], error_code=pkt[5]
    )


def parse_button_report(pkt: bytes) -> Optional[ButtonReport]:
    """Parse 0x30 button-only report: (a1) 30 BB BB"""
    if len(pkt) < 4 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_BTN:
        return None

    return ButtonReport(buttons=Buttons(u16be(pkt, 2)))


def parse_button_accel_report(pkt: bytes) -> Optional[ButtonAccelReport]:
    """Parse 0x31 button+accel report: (a1) 31 BB BB AA AA AA"""
    if len(pkt) < 7 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_BTN_ACC:
        return None

    return ButtonAccelReport(
        buttons=Buttons(u16be(pkt, 2)), accel_x=pkt[4], accel_y=pkt[5], accel_z=pkt[6]
    )


def parse_button_ext8_report(pkt: bytes) -> Optional[ButtonExt8Report]:
    """Parse 0x32 button+extension(8) report: (a1) 32 BB BB EE..."""
    if len(pkt) < 12 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_BTN_EXT8:
        return None

    return ButtonExt8Report(buttons=Buttons(u16be(pkt, 2)), extension=pkt[4:12])


def parse_button_accel_ir12_report(pkt: bytes) -> Optional[ButtonAccelIR12Report]:
    """Parse 0x33 button+accel+IR(12) report: (a1) 33 BB BB AA AA AA II..."""
    if len(pkt) < 19 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_BTN_ACC_IR12:
        return None

    return ButtonAccelIR12Report(
        buttons=Buttons(u16be(pkt, 2)),
        accel_x=pkt[4],
        accel_y=pkt[5],
        accel_z=pkt[6],
        ir_data=pkt[7:19],
    )


def parse_button_ext19_report(pkt: bytes) -> Optional[ButtonExt19Report]:
    """Parse 0x34 button+extension(19) report: (a1) 34 BB BB EE..."""
    if len(pkt) < 23 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_BTN_EXT19:
        return None

    return ButtonExt19Report(buttons=Buttons(u16be(pkt, 2)), extension=pkt[4:23])


def parse_button_accel_ext16_report(pkt: bytes) -> Optional[ButtonAccelExt16Report]:
    """Parse 0x35 button+accel+extension(16) report: (a1) 35 BB BB AA AA AA EE..."""
    if len(pkt) < 23 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_BTN_ACC_EXT16:
        return None

    return ButtonAccelExt16Report(
        buttons=Buttons(u16be(pkt, 2)),
        accel_x=pkt[4],
        accel_y=pkt[5],
        accel_z=pkt[6],
        extension=pkt[7:23],
    )


def parse_button_ir10_ext9_report(pkt: bytes) -> Optional[ButtonIR10Ext9Report]:
    """Parse 0x36 button+IR(10)+extension(9) report: (a1) 36 BB BB II... EE..."""
    if len(pkt) < 23 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_BTN_IR10_EXT9:
        return None

    return ButtonIR10Ext9Report(
        buttons=Buttons(u16be(pkt, 2)), ir_data=pkt[4:14], extension=pkt[14:23]
    )


def parse_button_accel_ir10_ext6_report(
    pkt: bytes,
) -> Optional[ButtonAccelIR10Ext6Report]:
    """Parse 0x37 button+accel+IR(10)+extension(6) report: (a1) 37 BB BB AA AA AA II... EE..."""
    if len(pkt) < 23 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_BTN_ACC_IR10_EXT6:
        return None

    return ButtonAccelIR10Ext6Report(
        buttons=Buttons(u16be(pkt, 2)),
        accel_x=pkt[4],
        accel_y=pkt[5],
        accel_z=pkt[6],
        ir_data=pkt[7:17],
        extension=pkt[17:23],
    )


def parse_ext21_report(pkt: bytes) -> Optional[Ext21Report]:
    """Parse 0x3D extension-only(21) report: (a1) 3d EE..."""
    if len(pkt) < 23 or pkt[0] != HID_INPUT or pkt[1] != WM_RPT_EXT21:
        return None

    return Ext21Report(extension=pkt[2:23])


def parse_button_accel_ir36_report(pkt: bytes) -> Optional[ButtonAccelIR36Report]:
    """Parse 0x3E/0x3F interleaved button+accel+IR(36) report: (a1) 3E/3F BB BB AA AA AA II..."""
    if (
        len(pkt) < 23
        or pkt[0] != HID_INPUT
        or pkt[1] not in (WM_RPT_BTN_ACC_IR36_1, WM_RPT_BTN_ACC_IR36_2)
    ):
        return None

    part = 1 if pkt[1] == WM_RPT_BTN_ACC_IR36_1 else 2

    return ButtonAccelIR36Report(
        buttons=Buttons(u16be(pkt, 2)),
        accel_x=pkt[4],
        accel_y=pkt[5],
        accel_z=pkt[6],
        ir_data=pkt[7:25] if len(pkt) >= 25 else pkt[7:],
        part=part,
    )


def parse_report(pkt: bytes) -> Optional[WiimoteReport]:
    """
    Parse any Wiimote input report.

    Args:
        pkt: Raw packet data starting with 0xa1 HID header

    Returns:
        Parsed report object, or None if invalid/unsupported
    """
    if len(pkt) < 2 or pkt[0] != HID_INPUT:
        return None

    report_id = pkt[1]

    parsers = {
        WM_RPT_STATUS: parse_status_report,
        WM_RPT_READ_MEMORY: parse_read_memory_report,
        WM_RPT_ACK: parse_ack_report,
        WM_RPT_BTN: parse_button_report,
        WM_RPT_BTN_ACC: parse_button_accel_report,
        WM_RPT_BTN_EXT8: parse_button_ext8_report,
        WM_RPT_BTN_ACC_IR12: parse_button_accel_ir12_report,
        WM_RPT_BTN_EXT19: parse_button_ext19_report,
        WM_RPT_BTN_ACC_EXT16: parse_button_accel_ext16_report,
        WM_RPT_BTN_IR10_EXT9: parse_button_ir10_ext9_report,
        WM_RPT_BTN_ACC_IR10_EXT6: parse_button_accel_ir10_ext6_report,
        WM_RPT_EXT21: parse_ext21_report,
        WM_RPT_BTN_ACC_IR36_1: parse_button_accel_ir36_report,
        WM_RPT_BTN_ACC_IR36_2: parse_button_accel_ir36_report,
    }

    parser = parsers.get(report_id)
    if parser:
        return parser(pkt)

    return None
