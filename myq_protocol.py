"""Small, dependency-free codecs for the observed MyQ Security 2.0 MQTT data.

This module intentionally does not invent the unverified gateway-startup reply.
"""
from __future__ import annotations

from dataclasses import dataclass

COMMAND_PREFIX = b"\x80\x02\x00\x01"
ACTION_OPEN = 1
ACTION_CLOSE = 0
STATE_NAMES = {1: "open", 2: "closed", 4: "opening", 5: "closing"}


@dataclass(frozen=True)
class DeviceTopics:
    serial: str

    def __post_init__(self) -> None:
        if not self.serial or "/" in self.serial:
            raise ValueError("serial must be a nonempty MQTT topic component")

    @property
    def command(self) -> str:
        return f"G/{self.serial}/011/0000"

    @property
    def telemetry(self) -> str:
        return f"S/0F/{self.serial}/011/0000"

    @property
    def startup_announce(self) -> str:
        return f"S/0F/{self.serial}/003/0000"

    @property
    def startup_response(self) -> str:
        return f"G/{self.serial}/012/0040"


def command_payload(opener_id: bytes, action: str) -> bytes:
    """Build the observed 12-byte door command for `open` or `close`."""
    if len(opener_id) != 6:
        raise ValueError("opener ID must be exactly six bytes")
    actions = {"open": ACTION_OPEN, "close": ACTION_CLOSE}
    try:
        code = actions[action.lower()]
    except KeyError as exc:
        raise ValueError("action must be 'open' or 'close'") from exc
    return opener_id + COMMAND_PREFIX + code.to_bytes(2, "little")


def decode_state(payload: bytes) -> str:
    """Decode the final little-endian telemetry word where observed."""
    if len(payload) < 2:
        raise ValueError("telemetry payload must contain a 16-bit state")
    value = int.from_bytes(payload[-2:], "little")
    if value == 3:
        return "stopped_unverified"
    return STATE_NAMES.get(value, f"unknown_{value}")
