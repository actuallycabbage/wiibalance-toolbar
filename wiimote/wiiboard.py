# Wii Balance Board Controller
# Extends Wiimote with Balance Board specific functionality

from __future__ import annotations

import asyncio
import struct
import zlib
from dataclasses import dataclass, field
from typing import Optional, Callable, Tuple

from . import reports
from .wiimote import Wiimote, WiimoteState, ReportMode


@dataclass
class CalibrationData:
    """Calibration data for Wii Balance Board sensors"""

    sensor_0kg: Tuple[int, int, int, int]   # TR, BR, TL, BL at 0kg
    sensor_17kg: Tuple[int, int, int, int]  # TR, BR, TL, BL at 17kg
    sensor_34kg: Tuple[int, int, int, int]  # TR, BR, TL, BL at 34kg
    reference_temperature: int  # Reference temperature for compensation
    reference_battery: int = 0x69  # Reference battery level (usually 0x69)

    def get_calibration(self, sensor_idx: int) -> Tuple[int, int, int]:
        """Get calibration values for a specific sensor (0-3)

        Args:
            sensor_idx: Sensor index (0=TR, 1=BR, 2=TL, 3=BL)

        Returns:
            Tuple of (0kg, 17kg, 34kg) calibration values
        """
        return (
            self.sensor_0kg[sensor_idx],
            self.sensor_17kg[sensor_idx],
            self.sensor_34kg[sensor_idx],
        )


@dataclass
class BalanceBoardState:
    """Current state of the Balance Board"""

    raw_tr: int = 0  # Top Right raw value
    raw_br: int = 0  # Bottom Right raw value
    raw_tl: int = 0  # Top Left raw value
    raw_bl: int = 0  # Bottom Left raw value

    temperature: int = 0  # Temperature reading
    battery_level: int = 0  # Battery level (0-255)

    weight_tr: float = 0.0  # Top Right weight in kg
    weight_br: float = 0.0  # Bottom Right weight in kg
    weight_tl: float = 0.0  # Top Left weight in kg
    weight_bl: float = 0.0  # Bottom Left weight in kg

    total_weight: float = 0.0  # Total weight in kg
    cob_x: float = 0.0  # Center of balance X coordinate
    cob_y: float = 0.0  # Center of balance Y coordinate


class WiiBoard(Wiimote):
    """
    Wii Balance Board controller extending Wiimote.

    Provides functionality to read weight data from the Balance Board's
    four pressure sensors and read calibration data from the EEPROM.
    """

    CALIBRATION_START_ADDR = 0xA40020
    CALIBRATION_END_ADDR = 0xA4003F
    REFERENCE_TEMP_ADDR = 0xA40060
    EXTENSION_INIT_ADDR_1 = 0xF0
    EXTENSION_INIT_ADDR_2 = 0xFB

    def __init__(self, connection):
        """
        Initialize Balance Board controller.

        Args:
            connection: Active Bluetooth connection to the Balance Board
        """
        super().__init__(connection)

        self._calibration: Optional[CalibrationData] = None
        self._bb_state = BalanceBoardState()
        self._last_state = BalanceBoardState()

        # Calibration data reassembly buffers
        self._calibration_buffer: bytearray = bytearray()
        self._expected_calibration_size = 0

        # Balance Board specific callbacks
        self._on_weight_change: Optional[Callable[[BalanceBoardState], None]] = None
        self._on_temperature_change: Optional[Callable[[int], None]] = None
        self._on_center_of_balance_change: Optional[Callable[[float, float], None]] = (
            None
        )
        self._on_calibration_loaded: Optional[Callable[[CalibrationData], None]] = None

        # Override extension data callback for weight parsing
        self.on_extension_data(self._handle_extension_data)

    # Properties

    @property
    def calibration(self) -> Optional[CalibrationData]:
        """Get calibration data (read-only)"""
        return self._calibration

    @property
    def total_weight(self) -> float:
        """Get current total weight in kg"""
        return self._bb_state.total_weight

    @property
    def center_of_balance(self) -> Tuple[float, float]:
        """Get current center of balance (x, y) coordinates"""
        return (self._bb_state.cob_x, self._bb_state.cob_y)

    @property
    def sensor_weights(self) -> dict[str, float]:
        """Get individual sensor weights as dictionary"""
        return {
            "top_right": self._bb_state.weight_tr,
            "bottom_right": self._bb_state.weight_br,
            "top_left": self._bb_state.weight_tl,
            "bottom_left": self._bb_state.weight_bl,
        }

    @property
    def temperature(self) -> int:
        """Get current temperature reading"""
        return self._bb_state.temperature

    @property
    def battery_level(self) -> int:
        """Get battery level (0-255)"""
        return self._bb_state.battery_level

    # Event callbacks

    def on_weight_change(self, callback: Callable[[BalanceBoardState], None]) -> None:
        """Set callback for weight changes"""
        self._on_weight_change = callback

    def on_temperature_change(self, callback: Callable[[int], None]) -> None:
        """Set callback for temperature changes"""
        self._on_temperature_change = callback

    def on_center_of_balance_change(
        self, callback: Callable[[float, float], None]
    ) -> None:
        """Set callback for center of balance changes"""
        self._on_center_of_balance_change = callback

    def on_calibration_loaded(
        self, callback: Callable[[CalibrationData], None]
    ) -> None:
        """Set callback when calibration data is loaded"""
        self._on_calibration_loaded = callback

    # Extension data handling

    def _handle_extension_data(self, data: bytes) -> None:
        """Handle extension data and update Balance Board state"""
        if len(data) >= 8:
            self._update_from_extension_data(data)

    def _update_from_extension_data(self, data: bytes) -> None:
        """Update state from 8 bytes of extension data"""
        # Parse 4 sensor values (16-bit big-endian each)
        raw_tr = struct.unpack_from(">H", data, 0)[0]
        raw_br = struct.unpack_from(">H", data, 2)[0]
        raw_tl = struct.unpack_from(">H", data, 4)[0]
        raw_bl = struct.unpack_from(">H", data, 6)[0]

        self._bb_state.raw_tr = raw_tr
        self._bb_state.raw_br = raw_br
        self._bb_state.raw_tl = raw_tl
        self._bb_state.raw_bl = raw_bl

        # Parse temperature and battery if available (report 0x34)
        if len(data) >= 11:
            self._bb_state.temperature = data[8]
            self._bb_state.battery_level = data[10]

        # Calculate weights if calibration is available
        if self._calibration:
            self._calculate_weights()

        # Trigger callbacks
        self._trigger_weight_callbacks()

    def _calculate_weights(self, compensate_temperature: bool = False) -> None:
        """Calculate weights from raw sensor readings"""
        # Calculate individual sensor weights
        self._bb_state.weight_tr = self.calculate_sensor_weight(
            self._bb_state.raw_tr, 0
        )
        self._bb_state.weight_br = self.calculate_sensor_weight(
            self._bb_state.raw_br, 1
        )
        self._bb_state.weight_tl = self.calculate_sensor_weight(
            self._bb_state.raw_tl, 2
        )
        self._bb_state.weight_bl = self.calculate_sensor_weight(
            self._bb_state.raw_bl, 3
        )

        # Calculate total weight
        self._bb_state.total_weight = (
            self._bb_state.weight_tr
            + self._bb_state.weight_br
            + self._bb_state.weight_tl
            + self._bb_state.weight_bl
        )

        # Apply temperature compensation if requested
        if compensate_temperature and self._calibration:
            temp_diff = (
                self._bb_state.temperature - self._calibration.reference_temperature
            )
            compensation_factor = 1.0 - 0.0007 * (temp_diff / 10.0)
            self._bb_state.total_weight *= compensation_factor

        # Calculate center of balance
        self._calculate_center_of_balance()

    def _calculate_center_of_balance(self) -> None:
        """Calculate center of balance from individual sensor weights"""
        total = self._bb_state.total_weight

        if total == 0:
            self._bb_state.cob_x = 0.0
            self._bb_state.cob_y = 0.0
            return

        # X coordinate: difference between left and right
        # Positive = more weight on right, Negative = more weight on left
        left_weight = self._bb_state.weight_tl + self._bb_state.weight_bl
        right_weight = self._bb_state.weight_tr + self._bb_state.weight_br
        self._bb_state.cob_x = ((right_weight - left_weight) / total) * 100.0

        # Y coordinate: difference between top and bottom
        # Positive = more weight on top, Negative = more weight on bottom
        top_weight = self._bb_state.weight_tl + self._bb_state.weight_tr
        bottom_weight = self._bb_state.weight_bl + self._bb_state.weight_br
        self._bb_state.cob_y = ((top_weight - bottom_weight) / total) * 100.0

    def _trigger_weight_callbacks(self) -> None:
        """Trigger weight-related callbacks if values changed"""
        # Check if weights changed
        if (
            self._bb_state.weight_tr != self._last_state.weight_tr
            or self._bb_state.weight_br != self._last_state.weight_br
            or self._bb_state.weight_tl != self._last_state.weight_tl
            or self._bb_state.weight_bl != self._last_state.weight_bl
        ):
            if self._on_weight_change:
                self._on_weight_change(self._bb_state)

        # Check if temperature changed
        if self._bb_state.temperature != self._last_state.temperature:
            if self._on_temperature_change:
                self._on_temperature_change(self._bb_state.temperature)

        # Check if center of balance changed
        if (
            self._bb_state.cob_x != self._last_state.cob_x
            or self._bb_state.cob_y != self._last_state.cob_y
        ):
            if self._on_center_of_balance_change:
                self._on_center_of_balance_change(
                    self._bb_state.cob_x, self._bb_state.cob_y
                )

        # Update last state
        self._last_state = BalanceBoardState(
            raw_tr=self._bb_state.raw_tr,
            raw_br=self._bb_state.raw_br,
            raw_tl=self._bb_state.raw_tl,
            raw_bl=self._bb_state.raw_bl,
            temperature=self._bb_state.temperature,
            battery_level=self._bb_state.battery_level,
            weight_tr=self._bb_state.weight_tr,
            weight_br=self._bb_state.weight_br,
            weight_tl=self._bb_state.weight_tl,
            weight_bl=self._bb_state.weight_bl,
            total_weight=self._bb_state.total_weight,
            cob_x=self._bb_state.cob_x,
            cob_y=self._bb_state.cob_y,
        )

    # Weight calculation methods

    def calculate_sensor_weight(self, reading: int, sensor_idx: int) -> float:
        """
        Calculate weight for a single sensor using calibration data.

        Args:
            reading: Raw sensor reading
            sensor_idx: Sensor index (0=TR, 1=BR, 2=TL, 3=BL)

        Returns:
            Calculated weight in kg
        """
        if not self._calibration:
            return 0.0

        cal_0kg, cal_17kg, cal_34kg = self._calibration.get_calibration(sensor_idx)

        # Interpolate based on calibration values
        if reading <= cal_0kg:
            # Below or at 0kg
            return 0.0
        elif reading <= cal_17kg:
            # Between 0kg and 17kg
            return 17.0 * (reading - cal_0kg) / (cal_17kg - cal_0kg)
        elif reading <= cal_34kg:
            # Between 17kg and 34kg
            return 17.0 + 17.0 * (reading - cal_17kg) / (cal_34kg - cal_17kg)
        else:
            # Above 34kg - extrapolate
            return 34.0 + 34.0 * (reading - cal_34kg) / (cal_34kg - cal_17kg)

    def get_center_of_balance(self) -> Tuple[float, float]:
        """
        Get the center of balance coordinates.

        Returns:
            Tuple of (x, y) coordinates as percentage offsets from center
            X: Positive = right, Negative = left
            Y: Positive = top, Negative = bottom
        """
        return self.center_of_balance

    # Calibration reading methods

    async def initialize(self, player: int = 1) -> None:
        """
        Initialize Balance Board with default settings.

        This method:
        1. Calls parent Wiimote initialization
        2. Initializes the extension controller
        3. Reads calibration data from EEPROM
        4. Sets appropriate report mode for weight data

        Args:
            player: Player number for LEDs (1-4)
        """
        # Call parent initialization
        await super().initialize(player)

        # Small delay to let initialization complete
        await asyncio.sleep(0.1)

        # Initialize extension controller
        await self._initialize_extension()
        await asyncio.sleep(0.05)

        # Read calibration data
        await self.read_calibration_data()
        await asyncio.sleep(0.15)

        # Set report mode to include extension data (8 bytes)
        await self.set_report_mode(ReportMode.BUTTONS_EXT8)

    async def _initialize_extension(self) -> None:
        """Initialize the Balance Board extension controller"""
        # Write initialization sequence
        await self.write_memory(
            self.EXTENSION_INIT_ADDR_1, bytes([0x55]), register_space=True
        )
        await asyncio.sleep(0.01)

        await self.write_memory(
            self.EXTENSION_INIT_ADDR_2, bytes([0x00]), register_space=True
        )

    async def read_calibration_data(self) -> None:
        """
        Read calibration data from Balance Board EEPROM.

        Reads:
        - 32 bytes from 0xa40020 to 0xa4003f (calibration points)
        - 2 bytes from 0xa40060 (reference temperature)

        Verifies CRC32 checksum before storing the data.
        """
        # Clear any existing calibration buffer
        self._calibration_buffer.clear()
        self._expected_calibration_size = (
            self.CALIBRATION_END_ADDR - self.CALIBRATION_START_ADDR + 1
        )

        # Read calibration data (32 bytes)
        await self.read_memory(
            self.CALIBRATION_START_ADDR,
            self._expected_calibration_size,
            register_space=True,
        )

        await asyncio.sleep(0.05)

        # Read reference temperature
        await self.read_memory(self.REFERENCE_TEMP_ADDR, 2, register_space=True)

        # Note: The actual data will be received via ReadMemoryReport
        # We'll handle it in _handle_read_memory_report if we override it

    # Report handling overrides

    def _handle_report(self, data: bytes) -> None:
        """Handle incoming report from Balance Board"""
        report = reports.parse_report(data)

        if report is None:
            return

        # Handle ReadMemoryReport for calibration data
        if isinstance(report, reports.ReadMemoryReport):
            self._handle_read_memory_report(report)
            return

        # Call parent handler for other report types
        super()._handle_report(data)

    def _handle_read_memory_report(self, report: reports.ReadMemoryReport) -> None:
        """Handle read memory report for calibration data"""
        # For register space reads, address field contains offset within requested range
        # Our 32-byte read at 0xa40020 returns two 16-byte chunks at offsets 0x0020 and 0x0030
        cal_offset_1 = self.CALIBRATION_START_ADDR & 0xFFFF  # 0x0020
        cal_offset_2 = (self.CALIBRATION_START_ADDR & 0xFFFF) + 16  # 0x0030
        temp_offset = self.REFERENCE_TEMP_ADDR & 0xFFFF  # 0x0060

        if report.address == cal_offset_1 or report.address == cal_offset_2:
            # Append data to calibration buffer (handles fragmented reads)
            self._calibration_buffer.extend(report.data)

            # Check if we've received all expected bytes
            if len(self._calibration_buffer) >= self._expected_calibration_size:
                self._process_calibration_data(
                    bytes(self._calibration_buffer[: self._expected_calibration_size])
                )
                self._calibration_buffer.clear()
        elif report.address == temp_offset and len(report.data) >= 2:
            self._process_reference_temperature(report.data)

    def _process_calibration_data(self, data: bytes) -> None:
        """Process calibration data from EEPROM"""
        # Parse calibration data
        # Format: unknown(1), ref_battery(1), padding(2), then 4 sensors × 3 points × 2 bytes, then checksum(4)
        sensor_0kg = (
            struct.unpack_from(">H", data, 4)[0],  # TR 0kg
            struct.unpack_from(">H", data, 6)[0],  # BR 0kg
            struct.unpack_from(">H", data, 8)[0],  # TL 0kg
            struct.unpack_from(">H", data, 10)[0],  # BL 0kg
        )

        sensor_17kg = (
            struct.unpack_from(">H", data, 12)[0],  # TR 17kg
            struct.unpack_from(">H", data, 14)[0],  # BR 17kg
            struct.unpack_from(">H", data, 16)[0],  # TL 17kg
            struct.unpack_from(">H", data, 18)[0],  # BL 17kg
        )

        sensor_34kg = (
            struct.unpack_from(">H", data, 20)[0],  # TR 34kg
            struct.unpack_from(">H", data, 22)[0],  # BR 34kg
            struct.unpack_from(">H", data, 24)[0],  # TL 34kg
            struct.unpack_from(">H", data, 26)[0],  # BL 34kg
        )

        reference_battery = data[1]

        # Store calibration data (reference temperature will be set separately)
        self._calibration = CalibrationData(
            sensor_0kg=sensor_0kg,
            sensor_17kg=sensor_17kg,
            sensor_34kg=sensor_34kg,
            reference_temperature=0,
            reference_battery=reference_battery,
        )

        # Store calibration data for checksum verification (will be completed when we read reference temp)
        self._cal_data_for_checksum = data  # Store the 32 bytes

    def _process_reference_temperature(self, data: bytes) -> None:
        """Process reference temperature from EEPROM"""
        if self._calibration:
            self._calibration.reference_temperature = data[0]

            # Verify checksum with reference temperature
            if hasattr(self, "_cal_data_for_checksum"):
                cal_data = self._cal_data_for_checksum

                # Extract data for checksum calculation (28 bytes total):
                # bytes 0x24-0x3b (24 bytes) + bytes 0x20-0x21 (2 bytes) + bytes 0x60-0x61 (2 bytes)
                cal_24_3b = cal_data[4:28]  # 0x24-0x3b
                cal_20_21 = cal_data[0:2]  # 0x20-0x21
                cal_60_61 = data[0:2]  # 0x60-0x61

                checksum_input = cal_24_3b + cal_20_21 + cal_60_61

                # Calculate CRC32
                calculated_crc = zlib.crc32(checksum_input) & 0xFFFFFFFF

                # Extract stored checksum (last 4 bytes of calibration data)
                stored_crc = struct.unpack(">I", cal_data[28:32])[0]

                # Verify and report
                if calculated_crc == stored_crc:
                    print(f"Calibration data verified!")
                    print(f"  Temperature: {data[0]}")
                    print(f"  Battery: {self._calibration.reference_battery}")
                    print(
                        f"  0kg: TR={self._calibration.sensor_0kg[0]}, BR={self._calibration.sensor_0kg[1]}, TL={self._calibration.sensor_0kg[2]}, BL={self._calibration.sensor_0kg[3]}"
                    )
                    print(
                        f"  17kg: TR={self._calibration.sensor_17kg[0]}, BR={self._calibration.sensor_17kg[1]}, TL={self._calibration.sensor_17kg[2]}, BL={self._calibration.sensor_17kg[3]}"
                    )
                    print(
                        f"  34kg: TR={self._calibration.sensor_34kg[0]}, BR={self._calibration.sensor_34kg[1]}, TL={self._calibration.sensor_34kg[2]}, BL={self._calibration.sensor_34kg[3]}"
                    )
                else:
                    print(f"Warning: Calibration data checksum verification failed")
                    print(
                        f"  Calculated: 0x{calculated_crc:08x}, Stored: 0x{stored_crc:08x}"
                    )

                delattr(self, "_cal_data_for_checksum")

            # Trigger calibration loaded callback
            if self._on_calibration_loaded:
                self._on_calibration_loaded(self._calibration)

    # Convenience methods

    async def get_total_weight(self, compensate_temperature: bool = False) -> float:
        """
        Get current total weight in kg.

        Args:
            compensate_temperature: Whether to apply temperature compensation

        Returns:
            Total weight in kg
        """
        if compensate_temperature:
            self._calculate_weights(compensate_temperature=True)

        return self.total_weight

    async def get_sensor_weights(self) -> dict[str, float]:
        """
        Get individual sensor weights.

        Returns:
            Dictionary mapping sensor names to weights in kg
        """
        return self.sensor_weights
