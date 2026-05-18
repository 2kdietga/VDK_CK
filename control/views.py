from __future__ import annotations

import json
from json import JSONDecodeError

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

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

    if name == 'set_output' and target:
        OutputTarget.objects.filter(key=target, is_enabled=True).update(
            current_state=params,
            updated_at=sent_at,
        )

    return JsonResponse(
        {
            'queued': True,
            'command_id': command['command_id'],
            'name': name,
            'params': params,
        },
        status=202,
    )
