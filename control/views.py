from __future__ import annotations

import json
from json import JSONDecodeError

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from gateway.consumers import build_command
from gateway.protocol import ESP32_GROUP_NAME

from .models import CommandLog, OutputTarget


@csrf_exempt
@require_POST
def send_command(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except JSONDecodeError:
        return JsonResponse({'error': 'Request body must be valid JSON.'}, status=400)

    name = payload.get('name')
    params = payload.get('params', {})

    if not isinstance(name, str) or not name:
        return JsonResponse({'error': '`name` is required and must be a string.'}, status=400)

    if not isinstance(params, dict):
        return JsonResponse({'error': '`params` must be an object.'}, status=400)

    validation_error = validate_command_payload(name, params)
    if validation_error:
        return JsonResponse({'error': validation_error}, status=400)

    command = build_command(name=name, params=params)
    target = params.get('target', '')
    if not isinstance(target, str):
        target = ''

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(ESP32_GROUP_NAME, command)
    sent_at = timezone.now()

    CommandLog.objects.create(
        command_id=command['command_id'],
        name=name,
        target=target,
        params=params,
        source=CommandLog.Source.MANUAL,
        status=CommandLog.Status.SENT,
        sent_at=sent_at,
    )

    return JsonResponse(
        {
            'queued': True,
            'awaiting_ack': True,
            'command_id': command['command_id'],
            'name': name,
            'params': params,
        },
        status=202,
    )


@require_GET
def command_status(request: HttpRequest, command_id: str) -> JsonResponse:
    try:
        command = CommandLog.objects.get(command_id=command_id)
    except CommandLog.DoesNotExist:
        return JsonResponse({'error': 'Command not found.'}, status=404)

    output_state = None
    if command.target:
        output = OutputTarget.objects.filter(key=command.target).first()
        if output is not None:
            output_state = output.current_state

    return JsonResponse(
        {
            'command_id': command.command_id,
            'name': command.name,
            'target': command.target,
            'params': command.params,
            'status': command.status,
            'sent_at': command.sent_at.isoformat() if command.sent_at else None,
            'completed_at': command.completed_at.isoformat() if command.completed_at else None,
            'output_state': output_state,
        }
    )


def validate_command_payload(name: str, params: dict) -> str | None:
    if name != 'set_output':
        return None

    target = params.get('target')
    if target not in {'led', 'fan'}:
        return '`params.target` must be "led" or "fan".'

    state = params.get('state')
    if not isinstance(state, bool):
        return '`params.state` is required and must be true or false.'

    value = params.get('value', 100 if state else 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return '`params.value` must be a number from 0 to 100.'

    if value < 0 or value > 100:
        return '`params.value` must be between 0 and 100.'

    params['value'] = round(value)
    return None
