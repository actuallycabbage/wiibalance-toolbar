# Stateful Wiimote Controller
# Maintains state and provides high-level interface for Wiimote operations

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Callable, Any
from enum import IntEnum

from . import reports
from .transport import BluetoothConnection, L2CAPChannel, PSM


@dataclass
class WiimoteState:
    """Represents the current state of a Wiimote"""

    # Button state
    buttons: reports.Buttons = field(default_factory=lambda: reports.Buttons(0))

    # Accelerometer state (raw 8-bit values)
    accel_x: int = 0
    accel_y: int = 0
    accel_z: int = 0

    # LED state
    led1: bool = False
    led2: bool = False
    led3: bool = False
    led4: bool = False

    # Battery and status
    battery_level: int = 0  # 0-255
    battery_low: bool = False

    # Extension and peripherals
    extension_connected: bool = False
    speaker_enabled: bool = False
    ir_enabled: bool = False

    # Extension data (if any)
    extension_data: bytes = b""

    # IR data (if any)
    ir_data: bytes = b""


class ReportMode(IntEnum):
    """Common Wiimote reporting modes"""

    BUTTONS_ONLY = reports.WM_RPT_BTN
    BUTTONS_ACCEL = reports.WM_RPT_BTN_ACC
    BUTTONS_EXT8 = reports.WM_RPT_BTN_EXT8
    BUTTONS_ACCEL_IR12 = reports.WM_RPT_BTN_ACC_IR12
    BUTTONS_EXT19 = reports.WM_RPT_BTN_EXT19
    BUTTONS_ACCEL_EXT16 = reports.WM_RPT_BTN_ACC_EXT16


class Wiimote:
    """
    High-level Wiimote controller with stateful tracking.

    Maintains internal state based on received reports and provides
    methods for sending commands to the Wiimote.
    """

    def __init__(self, connection: BluetoothConnection):
        """
        Initialize Wiimote controller.

        Args:
            connection: Active Bluetooth connection to the Wiimote
        """
        self._connection = connection
        self._state = WiimoteState()

        # L2CAP channels
        self._control_channel: Optional[L2CAPChannel] = None
        self._interrupt_channel: Optional[L2CAPChannel] = None

        # Event callbacks
        self._on_button_change: Optional[Callable[[reports.Buttons], None]] = None
        self._on_accel_change: Optional[Callable[[int, int, int], None]] = None
        self._on_status_change: Optional[Callable[[WiimoteState], None]] = None
        self._on_extension_data: Optional[Callable[[bytes], None]] = None

        # Connected state
        self._connected = False

    @property
    def state(self) -> WiimoteState:
        """Get the current Wiimote state (read-only copy)"""
        # Return a copy to prevent external modification
        return WiimoteState(
            buttons=self._state.buttons,
            accel_x=self._state.accel_x,
            accel_y=self._state.accel_y,
            accel_z=self._state.accel_z,
            led1=self._state.led1,
            led2=self._state.led2,
            led3=self._state.led3,
            led4=self._state.led4,
            battery_level=self._state.battery_level,
            battery_low=self._state.battery_low,
            extension_connected=self._state.extension_connected,
            speaker_enabled=self._state.speaker_enabled,
            ir_enabled=self._state.ir_enabled,
            extension_data=self._state.extension_data,
            ir_data=self._state.ir_data,
        )

    @property
    def connected(self) -> bool:
        """Check if Wiimote is connected"""
        return self._connected

    @property
    def buttons(self) -> reports.Buttons:
        """Get current button state"""
        return self._state.buttons

    @property
    def accelerometer(self) -> tuple[int, int, int]:
        """Get current accelerometer values (x, y, z)"""
        return (self._state.accel_x, self._state.accel_y, self._state.accel_z)

    @property
    def leds(self) -> tuple[bool, bool, bool, bool]:
        """Get current LED state (led1, led2, led3, led4)"""
        return (self._state.led1, self._state.led2, self._state.led3, self._state.led4)

    @property
    def battery_level(self) -> int:
        """Get battery level (0-255)"""
        return self._state.battery_level

    # Event callbacks

    def on_button_change(self, callback: Callable[[reports.Buttons], None]) -> None:
        """Set callback for button state changes"""
        self._on_button_change = callback

    def on_accel_change(self, callback: Callable[[int, int, int], None]) -> None:
        """Set callback for accelerometer changes"""
        self._on_accel_change = callback

    def on_status_change(self, callback: Callable[[WiimoteState], None]) -> None:
        """Set callback for status changes (battery, LEDs, etc.)"""
        self._on_status_change = callback

    def on_extension_data(self, callback: Callable[[bytes], None]) -> None:
        """Set callback for extension controller data"""
        self._on_extension_data = callback

    # Connection management

    async def connect(self) -> None:
        """
        Connect to the Wiimote and set up L2CAP channels.

        Must be called before using the Wiimote.
        """
        if self._connected:
            return

        # Create HID control channel (PSM 0x11)
        self._control_channel = await self._connection.create_l2cap_channel(
            PSM.HID_CONTROL
        )

        # Create HID interrupt channel (PSM 0x13)
        self._interrupt_channel = await self._connection.create_l2cap_channel(
            PSM.HID_INTERRUPT
        )

        # Set up interrupt channel to receive reports
        self._interrupt_channel.sink = self._handle_report

        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from the Wiimote"""
        if not self._connected:
            return

        await self._connection.disconnect()
        self._control_channel = None
        self._interrupt_channel = None
        self._connected = False

    # Report handling

    def _handle_report(self, data: bytes) -> None:
        """Handle incoming report from Wiimote"""
        report = reports.parse_report(data)

        if report is None:
            return

        # Update state based on report type
        if isinstance(report, reports.StatusReport):
            self._handle_status_report(report)
        elif isinstance(report, reports.ButtonReport):
            self._handle_button_report(report)
        elif isinstance(report, reports.ButtonAccelReport):
            self._handle_button_accel_report(report)
        elif isinstance(report, reports.ButtonExt8Report):
            self._handle_button_ext8_report(report)
        elif isinstance(report, reports.ButtonAccelIR12Report):
            self._handle_button_accel_ir12_report(report)
        elif isinstance(report, reports.ButtonExt19Report):
            self._handle_button_ext19_report(report)
        elif isinstance(report, reports.ButtonAccelExt16Report):
            self._handle_button_accel_ext16_report(report)
        elif isinstance(report, reports.AckReport):
            self._handle_ack_report(report)

    def _handle_status_report(self, report: reports.StatusReport) -> None:
        """Handle status report (0x20)"""
        self._state.buttons = report.buttons
        self._state.battery_level = report.battery_level
        self._state.battery_low = report.battery_low
        self._state.extension_connected = report.extension_connected
        self._state.speaker_enabled = report.speaker_enabled
        self._state.ir_enabled = report.ir_enabled
        self._state.led1 = report.led1
        self._state.led2 = report.led2
        self._state.led3 = report.led3
        self._state.led4 = report.led4

        if self._on_status_change:
            self._on_status_change(self.state)

    def _handle_button_report(self, report: reports.ButtonReport) -> None:
        """Handle button-only report (0x30)"""
        old_buttons = self._state.buttons
        self._state.buttons = report.buttons

        if old_buttons != report.buttons and self._on_button_change:
            self._on_button_change(report.buttons)

    def _handle_button_accel_report(self, report: reports.ButtonAccelReport) -> None:
        """Handle button + accelerometer report (0x31)"""
        old_buttons = self._state.buttons
        old_accel = (self._state.accel_x, self._state.accel_y, self._state.accel_z)

        self._state.buttons = report.buttons
        self._state.accel_x = report.accel_x
        self._state.accel_y = report.accel_y
        self._state.accel_z = report.accel_z

        if old_buttons != report.buttons and self._on_button_change:
            self._on_button_change(report.buttons)

        new_accel = (report.accel_x, report.accel_y, report.accel_z)
        if old_accel != new_accel and self._on_accel_change:
            self._on_accel_change(report.accel_x, report.accel_y, report.accel_z)

    def _handle_button_ext8_report(self, report: reports.ButtonExt8Report) -> None:
        """Handle button + extension(8) report (0x32)"""
        old_buttons = self._state.buttons
        self._state.buttons = report.buttons
        self._state.extension_data = report.extension

        if old_buttons != report.buttons and self._on_button_change:
            self._on_button_change(report.buttons)

        if self._on_extension_data:
            self._on_extension_data(report.extension)

    def _handle_button_accel_ir12_report(
        self, report: reports.ButtonAccelIR12Report
    ) -> None:
        """Handle button + accel + IR(12) report (0x33)"""
        old_buttons = self._state.buttons
        old_accel = (self._state.accel_x, self._state.accel_y, self._state.accel_z)

        self._state.buttons = report.buttons
        self._state.accel_x = report.accel_x
        self._state.accel_y = report.accel_y
        self._state.accel_z = report.accel_z
        self._state.ir_data = report.ir_data

        if old_buttons != report.buttons and self._on_button_change:
            self._on_button_change(report.buttons)

        new_accel = (report.accel_x, report.accel_y, report.accel_z)
        if old_accel != new_accel and self._on_accel_change:
            self._on_accel_change(report.accel_x, report.accel_y, report.accel_z)

    def _handle_button_ext19_report(self, report: reports.ButtonExt19Report) -> None:
        """Handle button + extension(19) report (0x34)"""
        old_buttons = self._state.buttons
        self._state.buttons = report.buttons
        self._state.extension_data = report.extension

        if old_buttons != report.buttons and self._on_button_change:
            self._on_button_change(report.buttons)

        if self._on_extension_data:
            self._on_extension_data(report.extension)

    def _handle_button_accel_ext16_report(
        self, report: reports.ButtonAccelExt16Report
    ) -> None:
        """Handle button + accel + extension(16) report (0x35)"""
        old_buttons = self._state.buttons
        old_accel = (self._state.accel_x, self._state.accel_y, self._state.accel_z)

        self._state.buttons = report.buttons
        self._state.accel_x = report.accel_x
        self._state.accel_y = report.accel_y
        self._state.accel_z = report.accel_z
        self._state.extension_data = report.extension

        if old_buttons != report.buttons and self._on_button_change:
            self._on_button_change(report.buttons)

        new_accel = (report.accel_x, report.accel_y, report.accel_z)
        if old_accel != new_accel and self._on_accel_change:
            self._on_accel_change(report.accel_x, report.accel_y, report.accel_z)

        if self._on_extension_data:
            self._on_extension_data(report.extension)

    def _handle_ack_report(self, report: reports.AckReport) -> None:
        """Handle acknowledgment report (0x22)"""
        # Update button state from ack
        self._state.buttons = report.buttons
        # TODO: Handle errors in report.error_code

    # Command methods

    def _send_command(self, data: bytes) -> None:
        """Send a command to the Wiimote via interrupt channel"""
        if not self._connected or not self._interrupt_channel:
            raise RuntimeError("Wiimote not connected")

        self._interrupt_channel.send(data)

    async def set_leds(
        self,
        led1: bool = False,
        led2: bool = False,
        led3: bool = False,
        led4: bool = False,
        *,
        rumble: bool = False,
    ) -> None:
        """
        Set LED state.

        Args:
            led1: LED 1 state
            led2: LED 2 state
            led3: LED 3 state
            led4: LED 4 state
            rumble: Enable rumble during command
        """
        payload = reports.set_leds(led1, led2, led3, led4, rumble=rumble)
        self._send_command(payload)

        # Optimistically update state (will be confirmed by status report)
        self._state.led1 = led1
        self._state.led2 = led2
        self._state.led3 = led3
        self._state.led4 = led4

    async def set_player_leds(self, player: int, *, rumble: bool = False) -> None:
        """
        Set LEDs to indicate player number (1-4).

        Args:
            player: Player number (1-4)
            rumble: Enable rumble during command
        """
        payload = reports.set_player_leds(player, rumble=rumble)
        self._send_command(payload)

    async def set_report_mode(
        self, mode: ReportMode, *, continuous: bool = True, rumble: bool = False
    ) -> None:
        """
        Set the data reporting mode.

        Args:
            mode: Reporting mode (from ReportMode enum)
            continuous: Continuous vs. on-change reporting
            rumble: Enable rumble during command
        """
        payload = reports.set_report_mode(
            mode.value, continuous=continuous, rumble=rumble
        )
        self._send_command(payload)

    async def rumble(self, enable: bool) -> None:
        """
        Control rumble motor.

        Args:
            enable: True to turn rumble on, False to turn off
        """
        payload = reports.rumble(enable)
        self._send_command(payload)

    async def request_status(self, *, rumble: bool = False) -> None:
        """
        Request status report from Wiimote.

        Wiimote will respond with a 0x20 status report containing
        battery level, LED state, and extension status.

        Args:
            rumble: Enable rumble during command
        """
        payload = reports.status_request(rumble=rumble)
        self._send_command(payload)

    async def read_memory(
        self,
        address: int,
        size: int,
        *,
        register_space: bool = False,
        rumble: bool = False,
    ) -> None:
        """
        Read memory/registers from Wiimote.

        Wiimote will respond with 0x21 read memory reports.

        Args:
            address: Memory address to read from
            size: Number of bytes to read
            register_space: True for register space, False for EEPROM
            rumble: Enable rumble during command
        """
        payload = reports.read_memory(
            address, size, register_space=register_space, rumble=rumble
        )
        self._send_command(payload)

    async def write_memory(
        self,
        address: int,
        data: bytes,
        *,
        register_space: bool = False,
        rumble: bool = False,
    ) -> None:
        """
        Write memory/registers to Wiimote.

        Args:
            address: Memory address to write to
            data: Data to write (max 16 bytes)
            register_space: True for register space, False for EEPROM
            rumble: Enable rumble during command
        """
        payload = reports.write_memory(
            address, data, register_space=register_space, rumble=rumble
        )
        self._send_command(payload)

    # Convenience methods

    async def initialize(self, player: int = 1) -> None:
        """
        Initialize Wiimote with default settings.

        Args:
            player: Player number for LEDs (1-4)
        """
        # Connect if not already connected
        if not self._connected:
            await self.connect()

        # Small delay to let connection stabilize
        await asyncio.sleep(0.1)

        # Set player LEDs
        await self.set_player_leds(player)
        await asyncio.sleep(0.05)

        # Set to buttons + accelerometer mode by default
        # await self.set_report_mode(ReportMode.BUTTONS_ONLY)
        await asyncio.sleep(0.05)

        # Request initial status
        await self.request_status()

        await asyncio.sleep(0.05)
