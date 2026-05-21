from __future__ import annotations

import json
from json import JSONDecodeError

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import (
    LLMConfigurationError,
    LLMIntentParseError,
    LLMProviderError,
    get_current_iot_context,
    parse_iot_intent,
)


@csrf_exempt
@require_POST
def intent_api(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except JSONDecodeError:
        return JsonResponse({'error': 'Request body must be valid JSON.'}, status=400)

    text = payload.get('text')
    if not isinstance(text, str) or not text.strip():
        return JsonResponse({'error': '`text` is required and must be a non-empty string.'}, status=400)

    try:
        intent = parse_iot_intent(text.strip(), context=get_current_iot_context())
    except LLMConfigurationError as exc:
        return JsonResponse({'error': str(exc)}, status=500)
    except LLMProviderError as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    except LLMIntentParseError as exc:
        return JsonResponse({'error': str(exc)}, status=422)

    return JsonResponse(intent)
