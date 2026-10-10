import sys
import os
import signal
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # TODO: what?

import flatbuffers
import asyncio

from WiimoteServer.BalanceFrame import BalanceFrameT
from WiimoteServer.ServerEnvelope import ServerEnvelopeT
from WiimoteServer.ServerMessage import ServerMessage

from wiimote.server import WiiDevice, WiimoteServer
from wiimote.wiiboard import WiiBoard

# The toolbar selects the NDJSON control plane with --json. The default entry
# point below retains the binary balance stream for existing consumers.

# steal stdout
wire = sys.stdout.buffer

# don't send any garbage to the consumer
sys.stdout = sys.stderr

def wire_send(data: bytes | bytearray):
    """Send a message payload over the wire"""
    wire.write(struct.pack(">I", len(data)))
    wire.write(data)
    wire.flush()

def on_cob(x: float, y: float) -> None:
    frame = BalanceFrameT(x=x, y=y)
    envelope = ServerEnvelopeT(messageType=ServerMessage.BalanceFrame, message=frame)
    builder = flatbuffers.Builder(64)
    builder.Finish(envelope.Pack(builder))
    buf = builder.Output()
    wire_send(buf)


async def main():
    print("hello!")
    def on_connect(mote: WiiDevice):
        print(f"[connect] {type(mote).__name__}", file=sys.stderr, flush=True)
        if not isinstance(mote, WiiBoard):
            return

        mote.on_center_of_balance_change(on_cob)

    # TODO(toolbar): Select the transport via server_start rather than claiming usb:0
    # at helper startup. Readiness/adapter enumeration must work before opening USB.
    server = WiimoteServer("usb:0", "")
    server.on_wiimote_connected(on_connect)
    await server.start(listen=True)

    # TODO(toolbar): Install shutdown handling before USB startup and handle stdin EOF;
    # stop/cancel must also clean up partially initialised connections and transports.
    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)
    loop.add_signal_handler(signal.SIGINT, stop.set_result, None)

    try:
        await stop
    finally:
        await server.stop()

if __name__ == "__main__":
    if "--json" in sys.argv:
        from app.server.control import main as control_main
        asyncio.run(control_main(wire))
    else:
        asyncio.run(main())
