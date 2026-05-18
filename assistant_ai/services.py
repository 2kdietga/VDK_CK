from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_OPENROUTER_MODEL = 'qwen/qwen-2.5-7b-instruct:free'
OPENROUTER_ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'
ALLOWED_ACTION_DEVICES = {'fan', 'led'}
ALLOWED_ACTION_COMMANDS = {'ON', 'OFF', 'PLAY'}
IOT_INTENT_PROMPT = '''Ban la tro ly AIoT.
Ban chi duoc phep dieu khien cac thiet bi co ma trong danh sach sau: fan, led.

Hay phan tich cau lenh cua nguoi dung va chi tra ve JSON hop le dung dang:
{{"action_device": "ma_thiet_bi", "action_command": "ON/OFF/PLAY"}}

Quy tac:
- "nong", "oi", "can lam mat" thuong la bat quat: fan ON.
- "toi", "thieu sang" thuong la bat den: led ON.
- "tat den" la led OFF.
- "tat quat" la fan OFF.
- Khong them markdown, khong boc trong ```json, khong giai thich.

Cau lenh nguoi dung: "{user_text}"'''

_last_llm_request_at = 0.0
_llm_rate_limit_lock = threading.Lock()


class LLMConfigurationError(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


class LLMIntentParseError(RuntimeError):
    pass


@dataclass(slots=True)
class LLMResponse:
    text: str
    raw: dict[str, Any]


def chat_with_llm(message: str) -> LLMResponse:
    provider = os.environ.get('LLM_PROVIDER', 'openrouter').lower()

    if provider == 'openrouter':
        return chat_with_openrouter(message)

    raise LLMConfigurationError(f'Unsupported LLM_PROVIDER: {provider}')


def parse_iot_intent(user_text: str) -> dict[str, str]:
    prompt = IOT_INTENT_PROMPT.format(user_text=user_text.replace('"', '\\"'))
    response = chat_with_llm(prompt)
    parsed = parse_json_object(response.text)

    action_device = parsed.get('action_device')
    action_command = parsed.get('action_command')

    if action_device not in ALLOWED_ACTION_DEVICES:
        raise LLMIntentParseError(f'Invalid action_device: {action_device!r}')

    if action_command not in ALLOWED_ACTION_COMMANDS:
        raise LLMIntentParseError(f'Invalid action_command: {action_command!r}')

    return {
        'action_device': action_device,
        'action_command': action_command,
    }


def chat_with_openrouter(message: str) -> LLMResponse:
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        raise LLMConfigurationError('OPENROUTER_API_KEY is not configured.')

    model = os.environ.get('OPENROUTER_MODEL', DEFAULT_OPENROUTER_MODEL)
    min_interval_seconds = env_float('LLM_MIN_REQUEST_INTERVAL_SECONDS', 2.0)
    payload = {
        'model': model,
        'messages': [
            {
                'role': 'user',
                'content': message,
            }
        ],
        'temperature': 0,
    }

    request = Request(
        OPENROUTER_ENDPOINT,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'HTTP-Referer': os.environ.get('OPENROUTER_SITE_URL', 'http://localhost'),
            'X-Title': os.environ.get('OPENROUTER_APP_NAME', 'AIoT Monitor'),
        },
        method='POST',
    )

    try:
        wait_between_llm_requests(min_interval_seconds)

        with urlopen(request, timeout=30) as response:
            raw = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        raise LLMProviderError(f'OpenRouter API error {exc.code}: {body}') from exc
    except URLError as exc:
        raise LLMProviderError(f'Could not connect to OpenRouter API: {exc.reason}') from exc

    return LLMResponse(text=extract_openrouter_text(raw), raw=raw)


def extract_openrouter_text(raw: dict[str, Any]) -> str:
    text = raw.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
    if not text:
        raise LLMProviderError('OpenRouter API returned no text.')
    return text


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def wait_between_llm_requests(min_interval_seconds: float) -> None:
    global _last_llm_request_at

    if min_interval_seconds <= 0:
        return

    with _llm_rate_limit_lock:
        now = time.monotonic()
        elapsed = now - _last_llm_request_at
        wait_seconds = min_interval_seconds - elapsed

        if wait_seconds > 0:
            time.sleep(wait_seconds)

        _last_llm_request_at = time.monotonic()


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`').strip()
        if cleaned.startswith('json'):
            cleaned = cleaned[4:].strip()

    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start == -1 or end == -1 or end < start:
        raise LLMIntentParseError('LLM response did not contain a JSON object.')

    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMIntentParseError('LLM response was not valid JSON.') from exc

    if not isinstance(parsed, dict):
        raise LLMIntentParseError('LLM response JSON must be an object.')

    return parsed
