from __future__ import annotations

import asyncio
import json
import uuid
from json import JSONDecodeError
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from automation.services import apply_automation_rule_requests, evaluate_automation_rules
from control.models import CommandLog, OutputTarget
from monitoring.alerts import evaluate_temperature_alert
from monitoring.models import SensorReading
from assistant_ai.services import (
    LLMConfigurationError,
    LLMIntentParseError,
    LLMProviderError,
    get_current_iot_context,
    parse_iot_intent,
)

from .audio import (
    AudioProcessingError,
    TTS_CHUNK_BYTES,
    fallback_tone_pcm,
    transcribe_pcm_chunks,
    voicerss_tts_pcm,
)
from .protocol import (
    ESP32_GROUP_NAME,
    MESSAGE_AUDIO_END,
    MESSAGE_COMMAND_ACK,
    MESSAGE_PING,
    MESSAGE_SENSOR_DATA,
    MESSAGE_STATUS,
    coerce_float,
    get_esp32_state,
)


class ESP32Consumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        self.audio_chunks: list[bytes] = []
        self.audio_bytes_received = 0
        self.audio_reply_tasks: set[asyncio.Task] = set()

        await self.channel_layer.group_add(ESP32_GROUP_NAME, self.channel_name)
        await self.accept()

        state = get_esp32_state()
        state.connected = True
        state.touch()

        await self.send_latest_output_commands()

    async def disconnect(self, close_code: int) -> None:
        await self.channel_layer.group_discard(ESP32_GROUP_NAME, self.channel_name)

        for task in self.audio_reply_tasks:
            task.cancel()

        state = get_esp32_state()
        state.connected = False
        state.touch()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        state = get_esp32_state()
        state.touch()

        if bytes_data is not None:
            state.audio_chunks_received += 1
            self.audio_chunks.append(bytes_data)
            self.audio_bytes_received += len(bytes_data)
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
            await self.apply_automation_rules(data)
            await self.apply_temperature_alert(data)
            return

        if message_type == MESSAGE_STATUS:
            state.last_status = payload
            return

        if message_type == MESSAGE_COMMAND_ACK:
            await self.handle_command_ack(payload)
            return

        if message_type == MESSAGE_AUDIO_END:
            await self.handle_audio_end()
            return

        if message_type == MESSAGE_PING:
            await self.send_json({'type': 'pong'})
            return

        await self.send_error('unknown_type', f'Unsupported message type: {message_type!r}.')

    async def handle_audio_end(self) -> None:
        if not self.audio_chunks:
            await self.send_error('empty_audio', 'Received audio_end but no binary audio chunks were buffered.')
            return

        chunks = self.audio_chunks
        self.audio_chunks = []
        self.audio_bytes_received = 0

        task = asyncio.create_task(self.process_audio_and_reply(chunks))
        self.audio_reply_tasks.add(task)
        task.add_done_callback(self.audio_reply_tasks.discard)

    async def process_audio_and_reply(self, chunks: list[bytes]) -> None:
        loop = asyncio.get_running_loop()

        try:
            user_text = await loop.run_in_executor(None, transcribe_pcm_chunks, chunks)
            context = await self.get_iot_context()
            intent = await loop.run_in_executor(None, parse_iot_intent, user_text, context)
            await self.send_voice_command(intent)
            reply_message = intent['reply_message']
            state = get_esp32_state()
            state.audio_requests_processed += 1
        except (AudioProcessingError, LLMConfigurationError, LLMProviderError, LLMIntentParseError) as exc:
            await self.send_error('voice_processing_failed', str(exc))
            reply_message = 'Bạn có thể nói lại được không.'

        await self.send_json({'type': 'stop_listen'})

        try:
            pcm = await loop.run_in_executor(None, voicerss_tts_pcm, reply_message)
        except AudioProcessingError as exc:
            await self.send_error('tts_failed', str(exc))
            pcm = fallback_tone_pcm()

        await self.send_audio_pcm(pcm)
        await asyncio.sleep(0.2)
        await self.send_json({'type': 'audio_done'})

    async def send_voice_command(self, intent: dict[str, Any]) -> None:
        automation_rules = intent.get('automation_rules')
        if isinstance(automation_rules, list) and automation_rules:
            await self.apply_voice_automation_rules(automation_rules)

        for command_name, params in commands_from_intent(intent):
            command = build_command(command_name, params)

            await self.server_command(command)
            await self.create_command_log(
                command_id=command['command_id'],
                name=command_name,
                target=params.get('target', ''),
                params=params,
                source=CommandLog.Source.VOICE,
            )

    async def send_audio_pcm(self, pcm: bytes) -> None:
        sent = 0
        while sent < len(pcm):
            chunk = pcm[sent : sent + TTS_CHUNK_BYTES]
            await self.send(bytes_data=chunk)
            sent += len(chunk)
            await asyncio.sleep(0.025)

    async def send_alert_audio(self, message: str) -> None:
        loop = asyncio.get_running_loop()
        await self.send_json({'type': 'stop_listen'})

        try:
            pcm = await loop.run_in_executor(None, voicerss_tts_pcm, message)
        except AudioProcessingError as exc:
            print(f'[ALERT TTS FAILED] {exc}', flush=True)
            pcm = fallback_tone_pcm()

        await self.send_audio_pcm(pcm)
        await asyncio.sleep(0.2)
        await self.send_json({'type': 'audio_done'})

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

    async def send_latest_output_commands(self) -> None:
        commands = await self.build_latest_output_commands()
        for command in commands:
            await self.server_command(command)

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

    @database_sync_to_async
    def create_command_log(
        self,
        command_id: str,
        name: str,
        target: str,
        params: dict[str, Any],
        source: str,
    ) -> None:
        CommandLog.objects.create(
            command_id=command_id,
            name=name,
            target=target if isinstance(target, str) else '',
            params=params,
            source=source,
            status=CommandLog.Status.SENT,
            sent_at=timezone.now(),
        )

    @database_sync_to_async
    def get_iot_context(self) -> dict[str, Any]:
        return get_current_iot_context()

    @database_sync_to_async
    def apply_voice_automation_rules(self, automation_rules: list[dict[str, Any]]) -> list[str]:
        return apply_automation_rule_requests(automation_rules)

    async def apply_automation_rules(self, sensor_data: dict[str, Any]) -> None:
        commands = await self.evaluate_automation_rules(sensor_data)
        for command in commands:
            await self.server_command(command)
            alert_message = command.get('automation_alert_message')
            if isinstance(alert_message, str) and alert_message.strip():
                print(
                    '[AUTOMATION ALERT] '
                    f"rule={command.get('automation_rule')} "
                    f"message={alert_message}",
                    flush=True,
                )
                await self.send_alert_audio(alert_message)

    @database_sync_to_async
    def evaluate_automation_rules(self, sensor_data: dict[str, Any]) -> list[dict[str, Any]]:
        return evaluate_automation_rules(sensor_data)

    async def apply_temperature_alert(self, sensor_data: dict[str, Any]) -> None:
        decision = await self.evaluate_temperature_alert(sensor_data)
        if not decision.get('should_alert'):
            return

        print(
            '[TEMPERATURE ALERT] '
            f"level={decision.get('level')} "
            f"risk={decision.get('risk_score')} "
            f"reasons={decision.get('reasons')} "
            f"message={decision.get('message')}",
            flush=True,
        )
        await self.send_alert_audio(decision['message'])

    @database_sync_to_async
    def evaluate_temperature_alert(self, sensor_data: dict[str, Any]) -> dict[str, Any]:
        decision = evaluate_temperature_alert(sensor_data)
        return {
            'should_alert': decision.should_alert,
            'key': decision.key,
            'level': decision.level,
            'message': decision.message,
            'risk_score': decision.risk_score,
            'reasons': decision.reasons,
        }

    @database_sync_to_async
    def build_latest_output_commands(self) -> list[dict[str, Any]]:
        outputs = {
            output.key: output
            for output in OutputTarget.objects.filter(key__in=['led', 'fan'], is_enabled=True)
        }
        commands: list[dict[str, Any]] = []
        sent_at = timezone.now()

        for target in ['led', 'fan']:
            output = outputs.get(target)
            if output is None:
                continue

            params = output.current_state if isinstance(output.current_state, dict) else {}
            params = {
                'target': target,
                'state': bool(params.get('state')),
                'value': params.get('value', 0),
            }
            command = build_command('set_output', params)
            commands.append(command)
            CommandLog.objects.create(
                command_id=command['command_id'],
                name=command['name'],
                target=target,
                params=params,
                source=CommandLog.Source.SYSTEM,
                status=CommandLog.Status.SENT,
                sent_at=sent_at,
            )

        return commands


def build_command(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'type': 'server.command',
        'command_id': uuid.uuid4().hex,
        'name': name,
        'params': params or {},
    }


def command_from_intent(intent: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    action = intent['action']
    device = intent['device']
    requested_value = intent.get('value')

    if action in {'turn_on', 'turn_off'} and device in {'light', 'fan'}:
        target = 'led' if device == 'light' else device
        state = action == 'turn_on'
        value = requested_value if isinstance(requested_value, int) else 100
        if not state and requested_value is None:
            value = 0

        return 'set_output', {
            'target': target,
            'state': state,
            'value': value,
        }

    return 'get_status', {'target': device}


def commands_from_intent(intent: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    raw_commands = intent.get('commands')
    if not isinstance(raw_commands, list):
        return [command_from_intent(intent)]

    commands = []
    for raw_command in raw_commands:
        if isinstance(raw_command, dict):
            commands.append(command_from_intent(raw_command))

    if not commands:
        return []

    return commands
