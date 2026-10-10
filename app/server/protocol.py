import json

# TODO(toolbar): Connect these models to server.py and implement menubar/BACKEND.md:
# command params, ready/capabilities, acknowledgements and typed state snapshots.
# This file is currently a sketch; the running server still emits FlatBuffers only.
from typing import Annotated, Any, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class BaseCommand(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int


# outgoing

class SuccessResponse(BaseModel):
    type: Literal["response"]
    id: int
    ok: Literal[True]
    result: Any


class ErrorResponse[T: Any](BaseModel):
    type: Literal["response"]
    id: int
    ok: Literal[False]
    error: T


class EventBase[T: str, U: Any](BaseModel):
    type: Literal["event"]
    event: T
    data: U


"""
Commands

USB:
    - list usb device
Transports
    - list transport
    - select transport
    - get selected transport
Server
    - start
    - status
    - stop
Motes
    - scan
    - pair
    - list paired (check auth key list)
    - disconnect
    - TBD: report mode?
"""


class ServerStartCommand(BaseCommand):
    method: Literal["server_start"]


class ServerStopCommand(BaseCommand):
    method: Literal["server_stop"]


class WiimotePairCommand(BaseCommand):
    method: Literal["wiimote_pair"]


Command = Annotated[
    Union[ServerStartCommand, ServerStopCommand, WiimotePairCommand],
    Field(discriminator="method"),
]

"""
Events

Wiimote
    - paired (mote initiated)
    - disconnected
    - button press
    - on cob change
    - on weight change
"""


class PairedEvent(EventBase):
    event: Literal["wiimote_paired"]


def parse_command(line: str) -> Command:
    adapter: TypeAdapter[Command] = TypeAdapter(Command)
    return adapter.validate_json(line)
