from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from groq import Groq


DEFAULT_GROQ_MODEL = 'llama-3.1-8b-instant'
ALLOWED_ACTIONS = {'turn_on', 'turn_off', 'get_status'}
ALLOWED_DEVICES = {'light', 'fan', 'sensor'}
IOT_INTENT_SYSTEM_PROMPT = '''Bạn là trợ lý hệ thống AIoT giám sát môi trường.
Nhiệm vụ của bạn là phân tích câu nói của người dùng và trả về ĐÚNG MỘT định dạng JSON, không kèm văn bản giải thích nào khác.

Cấu trúc JSON bắt buộc:
{
  "action": "turn_on" | "turn_off" | "get_status",
  "device": "light" | "fan" | "sensor",
  "reply_message": "Câu phản hồi ngắn gọn để phát ra loa cho người dùng"
}

Quy tắc:
- Câu có ý nóng, oi, cần làm mát thường là bật quạt: {"action":"turn_on","device":"fan"}.
- Câu có ý tối, thiếu sáng thường là bật đèn: {"action":"turn_on","device":"light"}.
- Câu hỏi nhiệt độ, độ ẩm, ánh sáng, môi trường hiện tại là lấy trạng thái cảm biến: {"action":"get_status","device":"sensor"}.
- Chỉ dùng đúng các action và device trong cấu trúc bắt buộc.'''

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
    provider = os.environ.get('LLM_PROVIDER', 'groq').lower()

    if provider == 'groq':
        return chat_with_groq(message)

    raise LLMConfigurationError(f'Unsupported LLM_PROVIDER: {provider}')


def parse_iot_intent(user_text: str) -> dict[str, str]:
    response = chat_with_llm(user_text)
    parsed = parse_json_object(response.text)

    action = parsed.get('action')
    device = parsed.get('device')
    reply_message = parsed.get('reply_message')

    if action not in ALLOWED_ACTIONS:
        raise LLMIntentParseError(f'Invalid action: {action!r}')

    if device not in ALLOWED_DEVICES:
        raise LLMIntentParseError(f'Invalid device: {device!r}')

    if not isinstance(reply_message, str) or not reply_message.strip():
        raise LLMIntentParseError('reply_message must be a non-empty string.')

    return {
        'action': action,
        'device': device,
        'reply_message': reply_message.strip(),
    }


def chat_with_groq(message: str) -> LLMResponse:
    api_key = os.environ.get('GROQ_API_KEY')
    if not api_key:
        raise LLMConfigurationError('GROQ_API_KEY is not configured.')

    model = os.environ.get('GROQ_MODEL', DEFAULT_GROQ_MODEL)
    min_interval_seconds = env_float('LLM_MIN_REQUEST_INTERVAL_SECONDS', 2.0)

    try:
        wait_between_llm_requests(min_interval_seconds)
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {
                    'role': 'system',
                    'content': IOT_INTENT_SYSTEM_PROMPT,
                },
                {
                    'role': 'user',
                    'content': message,
                },
            ],
            response_format={'type': 'json_object'},
            temperature=0.2,
            max_tokens=150,
        )
    except Exception as exc:
        raise LLMProviderError(f'Groq API error: {exc}') from exc

    raw = completion.model_dump()
    return LLMResponse(text=extract_groq_text(raw), raw=raw)


def extract_groq_text(raw: dict[str, Any]) -> str:
    text = raw.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
    if not text:
        raise LLMProviderError('Groq API returned no text.')
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
