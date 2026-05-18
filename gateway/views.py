from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from .protocol import get_esp32_state


@require_GET
def esp32_state(request: HttpRequest) -> JsonResponse:
    return JsonResponse(get_esp32_state().as_dict())
