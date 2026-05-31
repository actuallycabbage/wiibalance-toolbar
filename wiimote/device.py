from typing import Any, cast

from bumble.device import Device
from bumble.hci import (
    HCI_PIN_CODE_REQUEST_NEGATIVE_REPLY_COMMAND,
    HCI_PIN_CODE_REQUEST_REPLY_COMMAND,
    HCI_PIN_Code_Request_Reply_Command,
)
from bumble.host import Host


# TODO: un-monkeypatch this by making a custom Host
class WiimoteAwareDevice(Device):
    """
    Bumble device but with a monkeypatch to intercept auth negative replies with
    the custom wiimote pin format instead.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        HostCls = type(self.host)
        self._original_send = HostCls.send_command
        self._original_send_sync = HostCls.send_command_sync

    def enable_wiimote_pairing(self):
        def pin_reply_for(command):
            # Get the command opcode
            opcode = (
                getattr(command, "opcode", None)
                or getattr(command, "command_opcode", None)
                or getattr(command, "op_code", None)
            )
            name = command.__class__.__name__.lower()

            # print("*** intercepted opcode", opcode, name)

            # Check if this is a PIN negative reply
            is_pin_negative_reply = (
                opcode == HCI_PIN_CODE_REQUEST_NEGATIVE_REPLY_COMMAND
                or "pin_code_request_negative_reply" in name
            )

            if not is_pin_negative_reply:
                return None

            # host address for bonding request (use wiimote address for temp pair request)
            pin6 = _pin_from_address(self.public_address.to_string(False))
            pin16 = _pad16(pin6)

            # print(
            #     ">>> Wiimote pairing: Intercepted PIN request, "
            #     f"sending PIN {pin6.hex(' ')} for {command.bd_addr}"
            # )

            # print(
            #     "*** replaced intercepted opcode with",
            #     HCI_PIN_CODE_REQUEST_REPLY_COMMAND,
            #     "HCI_PIN_CODE_REQUEST_REPLY_COMMAND",
            # )

            return HCI_PIN_Code_Request_Reply_Command(command.bd_addr, 6, pin16)

        def patched_send(
            self_host: Host,
            command,
            check_result: bool = False,
            response_timeout: int | None = None,
        ):
            if reply := pin_reply_for(command):
                return self._original_send(
                    self_host, reply, check_result, response_timeout
                )

            return self._original_send(
                self_host, command, check_result, response_timeout
            )

        def patched_send_sync(self_host, command):
            if reply := pin_reply_for(command):
                return self._original_send_sync(self_host, reply)

            return self._original_send_sync(self_host, command)

        HostCls = type(self.host)
        HostCls.send_command = cast(Any, patched_send)
        HostCls.send_command_sync = cast(Any, patched_send_sync)

    def remove_wiimote_pairing(self):
        HostCls = type(self.host)
        HostCls.send_command = self._original_send
        HostCls.send_command_sync = self._original_send_sync


def _addr_bytes(addr: str) -> bytes:
    """Convert Bluetooth address string to bytes"""
    return bytes.fromhex(addr.split("/")[0].replace(":", ""))


def _pad16(b: bytes) -> bytes:
    """Pad PIN to 16 bytes"""
    if len(b) > 16:
        raise ValueError("PIN too long")
    return b + b"\x00" * (16 - len(b))


def _pin_from_address(addr: str) -> bytes:
    """
    Generate Wiimote PIN from Bluetooth address.

    Wiimotes use their own Bluetooth address (reversed) as the PIN code.
    This is the standard pairing method for Wiimotes.

    As per the wiibrew wiki, the wiimote has two authentication schemes:
    1. Pairing: The code for temporary pairing is the binary Bluetooth address
                of the Wiimote.
    2. Bonding: The PIN code for bonding is the binary Bluetooth address of the
                host machine.

    So, if you want to avoid hitting sync all the time take the bonding route
    and send the host address.

    ref: https://wiibrew.org/wiki/Wiimote#Bluetooth_Pairing
    """
    return _addr_bytes(addr)[::-1]  # 6 bytes, reversed
