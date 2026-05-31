import asyncio
import math
import os
import tempfile
import threading
from typing import Any, Optional, Tuple, cast

import bumble.apps.usb_probe
import bumble.logging
import rumps
import usb1
from click import Option
from PIL import Image, ImageDraw

from app.usb_probe import DeviceInfo, derive_device_info
from wiimote import server
from wiimote.server import WiimoteServer
from wiimote.wiiboard import WiiBoard
from wiimote.wiimote import Wiimote


class WiiBalanceToolbarApp(rumps.App):
    def __init__(self):
        super().__init__(
            "WiiBalanceToolbar",
            quit_button=cast(Any, None),  # this can actually be none the type is wrong
        )

        # Connect | Connecting | Disconnect
        # Wiiboard | No wiiboard [ greyed ]
        # Adapters  -> Refresh
        #              ------
        #              usbd:hjsdjs/4
        #              usb:

        # lock state when updating shared transport / device state between
        # threads
        self.device_lock = threading.Lock()

        self.device_scanning: bool = False
        self.device_scanning_thread: Optional[threading.Thread] = None

        self.transport_scanning: bool = False
        self.transport_result_thread: Optional[threading.Thread] = None
        self.transport_menu = rumps.MenuItem("Adapters")
        self.transport_menu.add(rumps.MenuItem("Scan", callback=self.adapter_probe))
        self.transport_menu.add(None)
        self.transport_name: Optional[str] = ""

        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.server_thread: Optional[threading.Thread] = None
        self.server: Optional[WiimoteServer] = None

        # self.connect_status_menu = rumps.MenuItem("Scan

        self.menu = [
            rumps.MenuItem("Connect", callback=self.on_connect_clicked),
            self.transport_menu,
            rumps.MenuItem("Disconnect", callback=self.on_disconnect_clicked),
            None,
            rumps.MenuItem("Quit", callback=self.on_quit_clicked),
        ]

        # immediately probe for usb devices
        self.adapter_probe(None)

        # TODO: for now just hardcoding to usb:0
        self.transport_name = "usb:0"

        self.board: Optional[WiiBoard] = None

        self.icon_timer = rumps.Timer(self.update_icon, 0.2)
        self.icon_timer.start()

        self._start_server_thread()

    def _start_server_thread(self):
        if self.server is not None:
            return

        self.server_thread = threading.Thread(
            target=lambda: asyncio.run(self._start_server()), daemon=True
        ).start()

    async def _start_server(self):
        """Run in separate thread"""
        self.event_loop
        if not self.transport_name:
            raise RuntimeError("trying to start server with no transport seleted")

        self.event_loop = asyncio.get_event_loop()
        self.server = WiimoteServer(
            transport_name=self.transport_name,
            transport_config_file="device-config.json",
        )

        self.server.on_wiimote_connected(self._on_wiimote_connected)

        await self.server.start()

        # TODO: set server state as running
        self.title = "Waiting..."

        try:
            await asyncio.get_running_loop().create_future()
        except asyncio.CancelledError:
            pass
        finally:
            await self.server.stop()

    def _on_wiimote_connected(self, mote: Wiimote | WiiBoard):
        if not isinstance(mote, WiiBoard):
            # no-op
            return

        self.board = mote

        # mote.on_center_of_balance_change(lambda x, y: self._on_cob_change((x,y)))

    def _on_cob_change(self, cob: Tuple[float, float]):
        if cob == (-100, 100):
            self.title = "---"

        self.title = f"{cob[0]:.0f}"
        print("COB: ", cob)

    def update_icon(self, _):
        if not self.board:
            # not connected
            return

        # grab cob and draw new icon
        cob = self.board.get_center_of_balance()

        self.title = None
        self.icon = ArrowIconGenerator.create_icon(cob[0], cob[1])

    async def _quit(self):
        """Shutdown"""
        item = rumps.MenuItem("Quitting...")
        item.state = False
        self.menu = [
            item,
        ]

        if self.server:
            await self.server.stop()

        rumps.quit_application()

    #
    # Menu Callbacks
    #

    def on_connect_clicked(self, _):
        pass

    def on_disconnect_clicked(self, _):
        pass

    def on_quit_clicked(self, _):
        # shutdown server
        if self.event_loop:
            asyncio.run_coroutine_threadsafe(self._quit(), self.event_loop)
        else:
            rumps.quit_application()

    #
    # Adapter management
    #
    def adapter_probe(self, _):
        """
        Kicks off probe job in a separate thread.
        """
        with self.device_lock:
            if self.transport_scanning:
                return

            self.transport_scanning = True

        thread = threading.Thread(target=self.adapter_probe_worker, daemon=True)

        self.transport_result_thread = thread
        thread.start()

    def adapter_probe_worker(self):
        with usb1.USBContext() as context:
            devices = [
                derive_device_info(device)
                for device in context.getDeviceIterator(skip_on_error=True)
                if device != None
            ]

        # update device list
        with self.device_lock:

            items: list[str] = []
            for device in devices:
                if device.is_bluetooth:
                    items.append(device.transport_names[-1])

            # replace from 2 onwards
            existing = self.transport_menu.values()
            self.transport_menu.clear()

            # put the first two back
            for item in existing[:2]:
                self.transport_menu.add(item)

            if len(items) == 0:
                item = rumps.MenuItem("No USB bluetooth devices found")
                item.state = False

                self.transport_menu.add(item)

            # list devices
            for adapter in items:
                # callback to set active?
                item = rumps.MenuItem(adapter)
                item.state = False

                self.transport_menu.add(item)

            # if there's no saved transport name in app state, select any
            # random adapter as the chosen adapter
            # TODO: check with saved state
            self.transport_name = items[-1]

            self.transport_scanning = False

    #
    # Board management
    #
    def board_probe(self, _):
        with self.device_lock:

            if self.transport_name is None:
                # can't scan for boards if there's no transport
                return

            if self.device_scanning:
                return

            self.device_scanning = True

        thread = threading.Thread(target=self.board_probe_worker, daemon=True)

        self.device_scanning_thread = thread

        thread.start()

    def board_probe_worker(self):
        pass

    def board_connect(self, _):
        pass

    def board_connect_worker(self):
        pass

    def board_disconnect(self, _):
        pass

    def board_disconnect_worker(self):
        pass


class ArrowIconGenerator:
    """Generate arrow icons for balance visualization"""

    ICON_SIZE = 22

    @staticmethod
    def get_color(distance: float) -> tuple:
        """Get color based on distance from center"""
        if distance < 20:
            return (0, 255, 0)  # Green
        elif distance < 50:
            return (255, 255, 0)  # Yellow
        else:
            return (255, 0, 0)  # Red

    @classmethod
    def create_icon(cls, cob_x: float = 0.0, cob_y: float = 0.0) -> str:
        """Create icon based on horizontal center of balance offset"""
        distance = abs(cob_x)

        img = Image.new("RGBA", (cls.ICON_SIZE, cls.ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        center = cls.ICON_SIZE // 2

        if distance < 5:
            cls._draw_circle(draw, center)
        else:
            angle = 0 if cob_x > 0 else 180
            color = cls.get_color(distance)
            cls._draw_arrow(draw, center, angle, color, distance)

        return cls._pil_to_rumps(img)

    @staticmethod
    def _draw_circle(draw, center: int):
        """Draw a circle for balanced state"""
        draw.ellipse(
            [(center - 4, center - 4), (center + 4, center + 4)],
            fill=(0, 255, 0),
            outline=(255, 255, 255),
            width=1,
        )

    @staticmethod
    def _draw_arrow(draw, center: int, angle: float, color: tuple, distance: float):
        """Draw an arrow pointing in the direction of balance offset"""
        length = min(8, 4 + distance * 0.15)
        thickness = 2
        head_size = 4

        rad = math.radians(angle)
        end_x = center + length * math.cos(rad)
        end_y = center + length * math.sin(rad)
        start_x = center - length * 0.3 * math.cos(rad)
        start_y = center - length * 0.3 * math.sin(rad)

        draw.line(
            [(start_x, start_y), (end_x, end_y)],
            fill=color,
            width=thickness,
        )

        head_rad1 = rad + math.radians(150)
        head_rad2 = rad - math.radians(150)
        head_x1 = end_x + head_size * math.cos(head_rad1)
        head_y1 = end_y + head_size * math.sin(head_rad1)
        head_x2 = end_x + head_size * math.cos(head_rad2)
        head_y2 = end_y + head_size * math.sin(head_rad2)

        draw.polygon(
            [(end_x, end_y), (head_x1, head_y1), (head_x2, head_y2)],
            fill=color,
        )

    @staticmethod
    def _pil_to_rumps(img: Image.Image) -> str:
        """Save PIL Image to temp file and return path"""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        img.save(path, format="PNG")
        return path


def main() -> None:
    app = WiiBalanceToolbarApp()
    app.run()


if __name__ == "__main__":
    bumble.logging.setup_basic_logging("INFO")

    main()
