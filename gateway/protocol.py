from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


MESSAGE_SENSOR_DATA = 'sensor_data'
MESSAGE_STATUS = 'status'
MESSAGE_PING = 'ping'
MESSAGE_COMMAND_ACK = 'command_ack'
MESSAGE_AUDIO_END = 'audio_end'
SENSOR_AVERAGE_WINDOW_SECONDS = 180
REALTIME_SENSOR_SAMPLE_LIMIT = 120


@dataclass(slots=True)
class SensorAccumulator:
    window_started_at: float | None = None
    count: int = 0
    temperature_total: float = 0.0
    temperature_count: int = 0
    humidity_total: float = 0.0
    humidity_count: int = 0
    light_total: float = 0.0
    light_count: int = 0

    def add(self, sample: dict[str, Any], timestamp: float) -> dict[str, Any] | None:
        if self.window_started_at is None:
            self.window_started_at = timestamp

        averaged_sample = None
        if timestamp - self.window_started_at >= SENSOR_AVERAGE_WINDOW_SECONDS and self.count > 0:
            averaged_sample = self.average(timestamp)
            self.reset(timestamp)

        self.count += 1
        self.add_value('temperature', sample.get('temperature'))
        self.add_value('humidity', sample.get('humidity'))
        self.add_value('light', sample.get('light'))
        return averaged_sample

    def add_value(self, field: str, value: Any) -> None:
        numeric_value = coerce_float(value)
        if numeric_value is None:
            return

        if field == 'temperature':
            self.temperature_total += numeric_value
            self.temperature_count += 1
        elif field == 'humidity':
            self.humidity_total += numeric_value
            self.humidity_count += 1
        elif field == 'light':
            self.light_total += numeric_value
            self.light_count += 1

    def average(self, timestamp: float) -> dict[str, Any]:
        return {
            'temperature': average_or_none(self.temperature_total, self.temperature_count),
            'humidity': average_or_none(self.humidity_total, self.humidity_count),
            'light': average_or_none(self.light_total, self.light_count),
            'sample_count': self.count,
            'window_started_at': self.window_started_at,
            'window_closed_at': timestamp,
            'window_seconds': SENSOR_AVERAGE_WINDOW_SECONDS,
        }

    def reset(self, timestamp: float) -> None:
        self.window_started_at = timestamp
        self.count = 0
        self.temperature_total = 0.0
        self.temperature_count = 0
        self.humidity_total = 0.0
        self.humidity_count = 0
        self.light_total = 0.0
        self.light_count = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            'window_started_at': self.window_started_at,
            'sample_count': self.count,
            'window_seconds': SENSOR_AVERAGE_WINDOW_SECONDS,
        }


@dataclass(slots=True)
class ESP32State:
    connected: bool = False
    last_seen: float | None = None
    latest_sensor: dict[str, Any] | None = None
    realtime_sensor_samples: list[dict[str, Any]] | None = None
    last_status: dict[str, Any] | None = None
    audio_chunks_received: int = 0
    audio_requests_processed: int = 0
    sensor_accumulator: SensorAccumulator | None = None

    def touch(self) -> None:
        self.last_seen = time.time()

    def record_sensor_sample(self, data: dict[str, Any]) -> dict[str, Any] | None:
        timestamp = time.time()
        sample = {
            'timestamp': timestamp,
            'temperature': coerce_float(data.get('temperature')),
            'humidity': coerce_float(data.get('humidity')),
            'light': coerce_float(data.get('light')),
            'raw_data': data,
        }

        self.latest_sensor = sample

        if self.realtime_sensor_samples is None:
            self.realtime_sensor_samples = []
        self.realtime_sensor_samples.append(sample)
        if len(self.realtime_sensor_samples) > REALTIME_SENSOR_SAMPLE_LIMIT:
            del self.realtime_sensor_samples[:-REALTIME_SENSOR_SAMPLE_LIMIT]

        if self.sensor_accumulator is None:
            self.sensor_accumulator = SensorAccumulator()
        return self.sensor_accumulator.add(sample, timestamp)

    def as_dict(self) -> dict[str, Any]:
        return {
            'connected': self.connected,
            'last_seen': self.last_seen,
            'latest_sensor': self.latest_sensor,
            'realtime_sensor_samples': self.realtime_sensor_samples or [],
            'last_status': self.last_status,
            'audio_chunks_received': self.audio_chunks_received,
            'audio_requests_processed': self.audio_requests_processed,
            'sensor_average': (
                self.sensor_accumulator.as_dict()
                if self.sensor_accumulator is not None
                else {
                    'window_started_at': None,
                    'sample_count': 0,
                    'window_seconds': SENSOR_AVERAGE_WINDOW_SECONDS,
                }
            ),
        }


ESP32_STATE = ESP32State()
ESP32_GROUP_NAME = 'esp32'


def get_esp32_state() -> ESP32State:
    return ESP32_STATE


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average_or_none(total: float, count: int) -> float | None:
    if count == 0:
        return None
    return total / count
