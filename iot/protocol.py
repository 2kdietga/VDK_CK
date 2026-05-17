from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


MESSAGE_SENSOR_DATA = 'sensor_data'
MESSAGE_STATUS = 'status'
MESSAGE_PING = 'ping'


@dataclass(slots=True)
class ESP32State:
    connected: bool = False
    last_seen: float | None = None
    latest_sensor: dict[str, Any] | None = None
    last_status: dict[str, Any] | None = None
    audio_chunks_received: int = 0

    def touch(self) -> None:
        self.last_seen = time.time()

    def as_dict(self) -> dict[str, Any]:
        return {
            'connected': self.connected,
            'last_seen': self.last_seen,
            'latest_sensor': self.latest_sensor,
            'last_status': self.last_status,
            'audio_chunks_received': self.audio_chunks_received,
        }


ESP32_STATE = ESP32State()
ESP32_GROUP_NAME = 'esp32'


def get_esp32_state() -> ESP32State:
    return ESP32_STATE
