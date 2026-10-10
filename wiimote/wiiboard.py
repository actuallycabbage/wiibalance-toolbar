# Wii Balance Board Controller
# Extends Wiimote with Balance Board specific functionality

from __future__ import annotations

import asyncio
import enum
import logging
import struct
import sys
import zlib
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from . import reports
from .wiimote import ReportMode, Wiimote

logger = logging.getLogger(__name__)


class SensorIndex(enum.IntEnum):
    TopRight = 0
    BottomRight = 1
    TopLeft = 2
    BottomLeft = 3


@dataclass
class CalibrationData:
    """Calibration data for Wii Balance Board sensors"""

    # Top Right (TR), Bottom Right (BR), Top Left (TL), Bottom Left (BL)
    sensor_0kg: Tuple[int, int, int, int]
    sensor_17kg: Tuple[int, int, int, int]
    sensor_34kg: Tuple[int, int, int, int]
    reference_temperature: int  # Reference temperature for compensation
    reference_battery: int = 0x69  # Reference battery level (usually 0x69)

    # todo: need a TARE feature
    def get_calibration(self, sensor_idx: SensorIndex) -> Tuple[int, int, int]:
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

    raw_tr: int = 0
    raw_br: int = 0
    raw_tl: int = 0
    raw_bl: int = 0

    temperature: int = 0  # in C
    battery_level: int = 0  # 0-255

    # in kg
    weight_tr: float = 0.0
    weight_br: float = 0.0
    weight_tl: float = 0.0
    weight_bl: float = 0.0

    # in kg
    total_weight: float = 0.0

    # -100 to 100
    cob_x: float = 0.0  # Center of balance X coordinate
    cob_y: float = 0.0  # Center of balance Y coordinate


class WiiBoard(Wiimote):
    """
    Wii Balance Board controller.

    The WiiBoard is an extension of the WiiMote with pressure sensor data
    available in the EXT8 report.
    """

    # caliration constants
    CALIBRATION_START_ADDR = 0xA40020
    CALIBRATION_END_ADDR = 0xA4003F
    REFERENCE_TEMP_ADDR = 0xA40060

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
        self._calibration_bytes: bytes = bytes()
        self._calibration_ready = asyncio.Event()
        self._calibration_error: Optional[str] = None
        self._initialisation_read: Optional[tuple[int, asyncio.Future]] = None
        self._sensors_all_zero: Optional[bool] = None
        self._last_status_battery: Optional[tuple[int, bool]] = None
        self._last_battery_bars: Optional[int] = None

        # Balance Board specific callbacks
        self._on_weight_change: Optional[Callable[[BalanceBoardState], None]] = None
        self._on_temperature_change: Optional[Callable[[int], None]] = None
        self._on_center_of_balance_change: Optional[Callable[[float, float], None]] = (
            None
        )
        self._on_calibration_loaded: Optional[Callable[[CalibrationData], None]] = None
        self._on_balance_report: Optional[Callable[[BalanceBoardState], None]] = None

        # Override extension data callback for weight parsing
        super().on_extension_data(self._handle_extension_data)

    # Properties

    @property
    def calibration(self) -> Optional[CalibrationData]:
        """Get calibration data"""
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

    @property
    def battery_bars(self) -> int:
        # WiiBrew documents bars for the extension battery byte, not a linear
        # percentage. These thresholds do not apply to the separate status byte.
        return sum(self.battery_level >= threshold for threshold in (0x6A, 0x78, 0x7D, 0x82))

    # Event callbacks

    def on_extension_data(self, callback: Any):
        """Internal use only"""
        pass

    def on_weight_change(self, callback: Callable[[BalanceBoardState], None]) -> None:
        """Set callback for weight changes"""
        self._on_weight_change = callback

    def on_balance_report(self, callback: Callable[[BalanceBoardState], None]) -> None:
        """Receive each calibrated report, even when balance has not changed."""
        self._on_balance_report = callback

    def on_temperature_change(self, callback: Callable[[int], None]) -> None:
        """Set callback for temperature changes"""
        self._on_temperature_change = callback

    # todo: this changes so rapidly may as well not even try check for changes and just toss it down the wire.
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

        all_zero = not any((raw_tr, raw_br, raw_tl, raw_bl))
        # A live Bluetooth stream can contain only zero sensor words. Log state
        # transitions, not every packet, to distinguish that from lost reporting.
        if all_zero != self._sensors_all_zero:
            print(
                f"[sensors] raw TR/BR/TL/BL={raw_tr}/{raw_br}/{raw_tl}/{raw_bl} "
                f"all_zero={all_zero} extension={data.hex(' ')}",
                file=sys.stderr,
                flush=True,
            )
            self._sensors_all_zero = all_zero

        self._bb_state.raw_tr = raw_tr
        self._bb_state.raw_br = raw_br
        self._bb_state.raw_tl = raw_tl
        self._bb_state.raw_bl = raw_bl

        # temperature and battery if available (report 0x34)
        if len(data) >= 11:
            self._bb_state.temperature = data[8]
            self._bb_state.battery_level = data[10]
            bars = self.battery_bars
            if bars != self._last_battery_bars:
                print(
                    f"[battery] Extension raw=0x{self.battery_level:02X} bars={bars}/4 "
                    f"temperature_raw=0x{self.temperature:02X}",
                    file=sys.stderr, flush=True,
                )
                self._last_battery_bars = bars

        # calculate weight data (takes calibration into account)
        if self._calibration:
            self._calculate_weights()

        # callbacks
        self._trigger_weight_callbacks()
        if self._calibration and self._on_balance_report:
            self._on_balance_report(self._bb_state)

    def _calculate_weights(self, compensate_temperature: bool = False) -> None:
        """Calculate weights from raw sensor readings"""
        self._bb_state.weight_tr = self.calculate_sensor_weight(
            self._bb_state.raw_tr, SensorIndex.TopRight
        )
        self._bb_state.weight_br = self.calculate_sensor_weight(
            self._bb_state.raw_br, SensorIndex.BottomRight
        )
        self._bb_state.weight_tl = self.calculate_sensor_weight(
            self._bb_state.raw_tl, SensorIndex.TopLeft
        )
        self._bb_state.weight_bl = self.calculate_sensor_weight(
            self._bb_state.raw_bl, SensorIndex.BottomLeft
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

    def calculate_sensor_weight(self, reading: int, sensor_idx: SensorIndex) -> float:
        """
        Calculate weight for a single sensor using calibration data.

        Args:
            reading: Raw sensor reading

        Returns:
            Calculated weight in kg
        """
        if not self._calibration:
            return 0.0

        cal_0kg, cal_17kg, cal_34kg = self._calibration.get_calibration(sensor_idx)

        # as per Wiibrew.org doc:

        # Calculating the weight on each sensor simply involves interpolating
        # between the two calibration values your reading falls between (or
        # using the higher two values if your reading exceeds the highest
        # calibration value), and the total weight on the board is the sum of
        # these values.

        def lerp(x, x0, x1, y0, y1):
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

        # lerp on calibration values
        if reading <= cal_0kg:
            return 0.0

        elif reading <= cal_17kg:
            return lerp(reading, cal_0kg, cal_17kg, 0.0, 17.0)

        elif reading <= cal_34kg:
            return lerp(reading, cal_17kg, cal_34kg, 17.0, 34.0)

        else:
            # extrapolate
            return lerp(reading, cal_17kg, cal_34kg, 17.0, 34.0)

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

        # 0x34 carries weight, temperature and battery; 0x32 stops after weight.
        await self.set_report_mode(ReportMode.BUTTONS_EXT19)

    async def _initialize_extension(self) -> None:
        """Initialize the Balance Board extension controller"""
        self._expected_calibration_size = 0
        # WiiBrew's F0/FB offsets are relative to 0xA40000. The packet
        # encoder sends addresses verbatim; register_space only selects the bus.
        # https://wiibrew.org/wiki/Wii_Balance_Board#Wii_Initialisation_Sequence
        await self.write_memory(0xA400F0, bytes([0x55]), register_space=True)
        print("[init] WiiBoard: acknowledged register write 0xA400F0 <- 55", file=sys.stderr, flush=True)

        await asyncio.sleep(0.01)

        await self.write_memory(0xA400FB, bytes([0x00]), register_space=True)
        print("[init] WiiBoard: acknowledged register write 0xA400FB <- 00", file=sys.stderr, flush=True)

        # WiiBrew reports that this observed Wii sequence revives disabled sensors.
        # F1's purpose is unknown. Its PC workaround explicitly omits F0 <- AA
        # (encryption enable), but retains the example key-register writes below.
        # Reads in the trace are 16 bytes. Wait durations are undocumented: use
        # 50 ms between steps and 100 ms at its two explicit settling waits.
        print("[init] WiiBoard: starting extended sensor workaround (encryption disabled)", file=sys.stderr, flush=True)
        await self._read_initialisation_register(0xA400FA)
        await asyncio.sleep(0.05)
        for offset, data in (
            (0x40, bytes.fromhex("ab 7c b4 1e 97 8d")),
            (0x46, bytes.fromhex("9e 7e 81 1b 99 29")),
            (0x4C, bytes.fromhex("17 07 a6 1e")),
            (0xF1, b"\xaa"),
            (0xF1, b"\xaa"),
            (0xF1, b"\xaa"),
        ):
            await self.write_memory(0xA40000 + offset, data, register_space=True)
            await asyncio.sleep(0.05)

        for offset in (0x20, 0x30):
            await self._read_initialisation_register(0xA40000 + offset)
            await asyncio.sleep(0.05)

        for data in (bytes.fromhex("aa aa aa 55 aa aa aa"), b"\xaa", b"\xaa"):
            await self.write_memory(0xA400F1, data, register_space=True)
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)
        await self.write_memory(0xA400F1, b"\xaa", register_space=True)

        for offset in (0x24, 0x34, 0x3C, 0x60, 0x21, 0x20, 0x24):
            await self._read_initialisation_register(0xA40000 + offset)
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)
        await self.write_memory(0xA400F1, b"\xaa", register_space=True)
        print("[init] WiiBoard: extended writes acknowledged; reading fresh calibration next", file=sys.stderr, flush=True)

    async def _read_initialisation_register(self, address: int) -> None:
        # Wait for each read response so a delayed workaround response cannot be
        # mistaken for a chunk of the subsequent authoritative calibration read.
        response = asyncio.get_running_loop().create_future()
        self._initialisation_read = (address & 0xFFFF, response)
        try:
            await self.read_memory(address, 16, register_space=True)
            await asyncio.wait_for(response, 3)
        finally:
            self._initialisation_read = None

    async def read_calibration_data(self) -> None:
        """
        Read calibration data from Balance Board EEPROM.

        Reads:
        - 32 bytes from 0xa40020 to 0xa4003f (calibration points)
        - 2 bytes from 0xa40060 (reference temperature)

        Verifies CRC32 checksum before storing the data.
        """
        # Clear any existing calibration buffer
        self._calibration_ready.clear()
        self._calibration_error = None
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
        await asyncio.wait_for(self._calibration_ready.wait(), 3)
        if self._calibration_error:
            raise RuntimeError(self._calibration_error)

    # Report handling overrides

    def _handle_status_report(self, report: reports.StatusReport) -> None:
        super()._handle_status_report(report)
        battery = (report.battery_level, report.battery_low)
        if battery != self._last_status_battery:
            print(
                f"[battery] Status raw=0x{report.battery_level:02X} low={report.battery_low}",
                file=sys.stderr, flush=True,
            )
            self._last_status_battery = battery

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
        if self._initialisation_read:
            address, response = self._initialisation_read
            if report.address == address and not response.done():
                if report.error or len(report.data) != 16:
                    response.set_exception(RuntimeError(f"Initialisation read 0xA4{address:04X} failed"))
                else:
                    response.set_result(None)
            return
        # The workaround also reads these registers, before the authoritative
        # calibration request. Do not assemble its overlapping reads as calibration.
        if not self._expected_calibration_size:
            return
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

        self._calibration = CalibrationData(
            sensor_0kg=sensor_0kg,
            sensor_17kg=sensor_17kg,
            sensor_34kg=sensor_34kg,
            reference_temperature=0,  # reference_temperature comes in in another report
            reference_battery=reference_battery,
        )

        # checksum verification comes in later when the full report is read
        self._calibration_bytes = data

    def _process_reference_temperature(self, data: bytes) -> None:
        """Process reference temperature from EEPROM"""

        if not self._calibration:
            return None

        if len(self._calibration_bytes) <= 0:
            return None

        self._calibration.reference_temperature = data[0]

        # validate crc
        if not self._validate_calibration_checksum(self._calibration_bytes, data):
            self._calibration_error = "Calibration CRC check failed"
        self._calibration_ready.set()
        if self._calibration_error:
            return

        # callback
        if self._on_calibration_loaded:
            self._on_calibration_loaded(self._calibration)

    def _validate_calibration_checksum(
        self, calibration_bytes: bytes, reference_temperature_bytes: bytes
    ):
        """
        Args:
            calibration_bytes: [0x20, 0x3f]
            reference_temperature_bytes: [0x60, 0x61]
        """

        cal_data = calibration_bytes

        # Extract data for checksum calculation (28 bytes total):
        # bytes 0x24-0x3b (24 bytes) + bytes 0x20-0x21 (2 bytes) + bytes 0x60-0x61 (2 bytes)

        cal_24_3b = cal_data[4:28]  # 0x24-0x3b
        cal_20_21 = cal_data[0:2]  # 0x20-0x21
        cal_60_61 = reference_temperature_bytes[0:2]  # 0x60-0x61

        checksum_input = cal_24_3b + cal_20_21 + cal_60_61

        calculated_crc = zlib.crc32(checksum_input) & 0xFFFFFFFF

        board_crc = struct.unpack(">I", cal_data[28:32])[0]

        if calculated_crc != board_crc:
            logger.warning(
                "Calibration check failure. Calculated %s Expected %s",
                calculated_crc,
                board_crc,
            )
            return False

        return True
