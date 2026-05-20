from __future__ import annotations

import json
import uuid
from json import JSONDecodeError
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from control.models import CommandLog, OutputTarget
from monitoring.models import SensorReading

from .protocol import (
    ESP32_GROUP_NAME,
    MESSAGE_COMMAND_ACK,
    MESSAGE_PING,
    MESSAGE_SENSOR_DATA,
    MESSAGE_STATUS,
    coerce_float,
    get_esp32_state,
)


class ESP32Consumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        await self.channel_layer.group_add(ESP32_GROUP_NAME, self.channel_name)
        await self.accept()

        state = get_esp32_state()
        state.connected = True
        state.touch()

    async def disconnect(self, close_code: int) -> None:
        await self.channel_layer.group_discard(ESP32_GROUP_NAME, self.channel_name)

        state = get_esp32_state()
        state.connected = False
        state.touch()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        state = get_esp32_state()
        state.touch()

        if bytes_data is not None:
            state.audio_chunks_received += 1
            return

        if text_data is None:
            await self.send_error('empty_message', 'Message must contain JSON text or binary audio.')
            return

        try:
            payload = json.loads(text_data)
        except JSONDecodeError:
            await self.send_error('invalid_json', 'Text WebSocket messages must be valid JSON.')
            return

        if not isinstance(payload, dict):
            await self.send_error('invalid_payload', 'JSON payload must be an object.')
            return

        await self.handle_json_message(payload)

    async def handle_json_message(self, payload: dict[str, Any]) -> None:
        message_type = payload.get('type')
        state = get_esp32_state()

        if message_type == MESSAGE_SENSOR_DATA:
            data = payload.get('data')
            if not isinstance(data, dict):
                await self.send_error('invalid_sensor_data', '`data` must be an object.')
                return

            averaged_data = state.record_sensor_sample(data)
            if averaged_data is not None:
                await self.save_sensor_reading(averaged_data)
            return

        if message_type == MESSAGE_STATUS:
            state.last_status = payload
            return

        if message_type == MESSAGE_COMMAND_ACK:
            await self.handle_command_ack(payload)
            return

        if message_type == MESSAGE_PING:
            await self.send_json({'type': 'pong'})
            return

        await self.send_error('unknown_type', f'Unsupported message type: {message_type!r}.')

    async def handle_command_ack(self, payload: dict[str, Any]) -> None:
        command_id = payload.get('command_id')
        if not isinstance(command_id, str) or not command_id:
            await self.send_error('invalid_command_ack', '`command_id` is required for command_ack.')
            return

        status = payload.get('status', CommandLog.Status.COMPLETED)
        if status not in {CommandLog.Status.COMPLETED, CommandLog.Status.FAILED}:
            status = CommandLog.Status.COMPLETED

        params = payload.get('params')
        if params is not None and not isinstance(params, dict):
            await self.send_error('invalid_command_ack', '`params` must be an object when provided.')
            return

        updated = await self.apply_command_ack(command_id, status, params)
        if not updated:
            await self.send_error('unknown_command', f'No command found for command_id: {command_id}.')

    async def server_command(self, event: dict[str, Any]) -> None:
        await self.send_json(
            {
                'type': 'command',
                'command_id': event['command_id'],
                'name': event['name'],
                'params': event.get('params', {}),
            }
        )

    async def server_audio(self, event: dict[str, Any]) -> None:
        await self.send(bytes_data=event['bytes'])

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(payload))

    async def send_error(self, code: str, message: str) -> None:
        await self.send_json(
            {
                'type': 'error',
                'error': {
                    'code': code,
                    'message': message,
                },
            }
        )

    @database_sync_to_async
    def save_sensor_reading(self, data: dict[str, Any]) -> None:
        SensorReading.objects.create(
            temperature=coerce_float(data.get('temperature')),
            humidity=coerce_float(data.get('humidity')),
            light=coerce_float(data.get('light')),
            raw_data=data,
        )

    @database_sync_to_async
    def apply_command_ack(
        self,
        command_id: str,
        status: str,
        ack_params: dict[str, Any] | None,
    ) -> bool:
        try:
            command = CommandLog.objects.get(command_id=command_id)
        except CommandLog.DoesNotExist:
            return False

        completed_at = timezone.now()
        command.status = status
        command.completed_at = completed_at
        command.save(update_fields=['status', 'completed_at'])

        params = ack_params if ack_params is not None else command.params
        target = params.get('target') or command.target
        if status == CommandLog.Status.COMPLETED and command.name == 'set_output' and target:
            OutputTarget.objects.filter(key=target, is_enabled=True).update(
                current_state=params,
                updated_at=completed_at,
            )

        return True


def build_command(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'type': 'server.command',
        'command_id': uuid.uuid4().hex,
        'name': name,
        'params': params or {},
    }
