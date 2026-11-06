"""Device message codecs copied into the standalone Home Assistant package."""
from __future__ import annotations

from dataclasses import dataclass

COMMAND_PREFIX = b"\x80\x02\x00\x01"
STATE_NAMES = {1: "open", 2: "closed", 4: "opening", 5: "closing"}


@dataclass(frozen=True)
class DeviceTopics:
    serial: str

    @property
    def command(self) -> str:
        return f"G/{self.serial}/011/0000"

    @property
    def telemetry(self) -> str:
        return f"S/0F/{self.serial}/011/0000"


def command_payload(opener_id: bytes, action: str) -> bytes:
    if len(opener_id) != 6:
        raise ValueError("opener ID must be six bytes")
    code = {"open": 1, "close": 0}.get(action.lower())
    if code is None:
        raise ValueError("action must be open or close")
    return opener_id + COMMAND_PREFIX + code.to_bytes(2, "little")


def decode_state(payload: bytes) -> str:
    if len(payload) < 2:
        raise ValueError("state payload must contain a 16-bit value")
    value = int.from_bytes(payload[-2:], "little")
    return "stopped_unverified" if value == 3 else STATE_NAMES.get(value, f"unknown_{value}")
