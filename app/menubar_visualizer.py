#!/usr/bin/env python3
import argparse
import asyncio
import math
import os
import sys
import tempfile
import threading
from io import BytesIO

from PIL import Image, ImageDraw
import rumps

import bumble.logging

from wiimote import create_transport, WiiBoard


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


class BalanceBoardMenuBar(rumps.App):
    """macOS menubar app for Wii Balance Board visualization"""

    def __init__(self):
        super().__init__("⚪")

        self.board: WiiBoard | None = None
        self.connected = False
        self.cob_x = 0.0
        self.cob_y = 0.0
        self.total_weight = 0.0
        self.connection_lock = threading.Lock()
        self.event_loop: asyncio.AbstractEventLoop | None = None
        self.config_file: str = ""
        self.transport_name: str = ""
        self.peer_address: str = ""

        self.coords_menu = rumps.MenuItem("X: 0.0, Y: 0.0", callback=None)
        self.weight_menu = rumps.MenuItem("Weight: 0.0 kg", callback=None)
        self.menu = [
            rumps.MenuItem("Connect", callback=self.connect),
            rumps.MenuItem("Disconnect", callback=self.disconnect),
            None,
            rumps.MenuItem("Status", callback=self.show_status),
            self.weight_menu,
            self.coords_menu,
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        self.timer = rumps.Timer(self.update_icon, 0.2)
        self.timer.start()

    @rumps.clicked("Connect")
    def connect(self, _):
        """Connect to Wii Balance Board"""
        if self.connected:
            rumps.notification(
                title="Balance Board", subtitle="", message="Already connected"
            )
            return

        def run_connect():
            asyncio.run(self._async_connect())

        threading.Thread(target=run_connect, daemon=True).start()

    async def _async_connect(self):
        """Async connection logic"""
        try:
            self.event_loop = asyncio.get_event_loop()

            async with create_transport(
                self.config_file, self.transport_name
            ) as transport:
                print(f"<<< Transport started (local address: {transport.address})")

                print(f"=== Connecting to {self.peer_address}...")
                connection = await transport.connect(self.peer_address)
                print(f"=== Connected to {connection.peer_address}")

                self.board = WiiBoard(connection)

                def on_center_of_balance_change(x: float, y: float):
                    with self.connection_lock:
                        self.cob_x = x
                        self.cob_y = y

                def on_weight_change(state):
                    with self.connection_lock:
                        self.total_weight = state.total_weight

                self.board.on_weight_change(on_weight_change)
                self.board.on_center_of_balance_change(on_center_of_balance_change)

                print("=== Initializing Balance Board...")
                await self.board.initialize(player=1)
                print("=== Balance Board initialized")

                self.connected = True
                rumps.notification(
                    title="Balance Board", subtitle="", message="Connected successfully"
                )

                await asyncio.Future()

        except Exception as e:
            print(f"Connection error: {e}")
            rumps.notification(
                title="Balance Board", subtitle="", message=f"Connection failed: {e}"
            )
            self.connected = False

    @rumps.clicked("Disconnect")
    def disconnect(self, _):
        """Disconnect from Wii Balance Board"""
        if not self.connected or self.board is None:
            rumps.notification(
                title="Balance Board", subtitle="", message="Not connected"
            )
            return

        board = self.board

        async def _disconnect():
            await board.disconnect()
            self.connected = False
            self.board = None
            self.cob_x = 0.0
            self.cob_y = 0.0
            self.total_weight = 0.0
            rumps.notification(
                title="Balance Board", subtitle="", message="Disconnected"
            )

        def run_disconnect():
            if self.event_loop and not self.event_loop.is_closed():
                asyncio.run_coroutine_threadsafe(_disconnect(), self.event_loop).result(
                    timeout=5
                )

        threading.Thread(target=run_disconnect, daemon=True).start()

    def update_icon(self, _):
        """Update menubar icon with current balance state"""
        if not self.connected:
            self.icon = None
            self.title = "⚪"
            self.weight_menu.title = "Weight: 0.0 kg"
            self.coords_menu.title = "X: 0.0, Y: 0.0"
            return

        with self.connection_lock:
            cob_x = self.cob_x
            cob_y = self.cob_y
            total_weight = self.total_weight

        self.icon = ArrowIconGenerator.create_icon(cob_x, cob_y)
        self.title = None
        self.weight_menu.title = f"Weight: {total_weight:.1f} kg"
        self.coords_menu.title = f"X: {cob_x:.1f}, Y: {cob_y:.1f}"

    @rumps.clicked("Status")
    def show_status(self, _):
        """Show connection status"""
        if self.connected:
            with self.connection_lock:
                cob_x = self.cob_x
                cob_y = self.cob_y
                total_weight = self.total_weight

            rumps.alert(
                title="Balance Board Status",
                message=f"Connected\nWeight: {total_weight:.1f} kg\nCoB X: {cob_x:.1f}\nCoB Y: {cob_y:.1f}\nDistance: {math.sqrt(cob_x**2 + cob_y**2):.1f}",
                ok="Close",
            )
        else:
            rumps.alert(
                title="Balance Board Status", message="Disconnected", ok="Close"
            )

    @rumps.clicked("Quit")
    def quit_app(self, _):
        """Quit the application"""
        if self.connected and self.board:
            board = self.board

            async def cleanup():
                await board.disconnect()

            if self.event_loop and not self.event_loop.is_closed():
                try:
                    asyncio.run_coroutine_threadsafe(cleanup(), self.event_loop).result(
                        timeout=3
                    )
                except:
                    pass

        rumps.quit_application()


def main(config_file: str, transport_name: str, peer_address: str) -> None:
    """Run the menubar app"""
    app = BalanceBoardMenuBar()
    app.config_file = config_file
    app.transport_name = transport_name
    app.peer_address = peer_address

    app.run()


if __name__ == "__main__":
    bumble.logging.setup_basic_logging("INFO")

    parser = argparse.ArgumentParser(description="Wii Balance Board menubar visualizer")
    parser.add_argument("config", help="Bumble device configuration file")
    parser.add_argument("transport", help="Bumble transport (e.g., usb:0)")
    parser.add_argument(
        "peer_address",
        help="Balance Board Bluetooth address (e.g., 00:25:A0:41:39:0A/P)",
    )
    args = parser.parse_args(sys.argv[1:])

    main(args.config, args.transport, args.peer_address)
