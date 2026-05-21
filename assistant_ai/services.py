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
IOT_INTENT_SYSTEM_PROMPT = '''Bạn là trợ lý AIoT dùng cho hệ thống giám sát môi trường ESP32.
Nhiệm vụ của bạn là phân tích câu nói tiếng Việt của người dùng, sau đó trả về đúng một object JSON duy nhất.
Không được thêm markdown, giải thích, chú thích hay bất kỳ nội dung nào ngoài JSON.

Required JSON shape:
{
  "action": "turn_on" | "turn_off" | "get_status",
  "device": "light" | "fan" | "sensor",
  "value": 0-100 | null,
  "reply_message": "short Vietnamese reply for text-to-speech"
}

Quy tắc xử lý:

1. Điều khiển quạt:
- Nếu người dùng nói nóng, oi, bí, ngột ngạt, khó chịu, cần mát hơn → bật quạt.
- Nếu người dùng nói lạnh, mát rồi, thoáng rồi, không cần quạt, tắt gió → tắt quạt.
- Nóng thì bật quạt, lạnh thì tắt quạt.

2. Điều khiển đèn:
- Nếu người dùng nói tối, hơi tối, thiếu sáng, bật đèn, sáng hơn → bật đèn.
- Nếu người dùng nói tắt đèn, không cần đèn, sáng quá → tắt đèn.

3. Hỏi trạng thái cảm biến:
- Nếu người dùng hỏi về nhiệt độ, độ ẩm, ánh sáng, môi trường, trạng thái phòng, thông số cảm biến → lấy trạng thái cảm biến.
- Khi lấy trạng thái cảm biến:
  - "action": "get_status"
  - "device": "sensor"
  - "value": null

4. Xử lý phần trăm hoặc mức:
- Nếu người dùng nói phần trăm hoặc mức từ 0 đến 100, đưa giá trị đó vào "value".
- Ví dụ:
  - "bật đèn 50%"
  - "bật đèn độ sáng 50"
  - "đèn năm mươi phần trăm"
  → {"action":"turn_on","device":"light","value":50}

- Ví dụ:
  - "bật quạt 70%"
  - "cho quạt 70"
  - "quạt bảy mươi phần trăm"
  → {"action":"turn_on","device":"fan","value":70}

5. Nếu không có phần trăm hoặc mức:
- Với lệnh bật thiết bị, dùng "value": null.
- Với lệnh hỏi trạng thái, dùng "value": null.
- Với lệnh tắt thiết bị, dùng "value": 0, trừ khi người dùng nói rõ một giá trị hợp lệ khác.

6. Giá trị hợp lệ:
- "action" chỉ được là một trong các giá trị:
  - "turn_on"
  - "turn_off"
  - "get_status"

- "device" chỉ được là một trong các giá trị:
  - "light"
  - "fan"
  - "sensor"

7. Phản hồi:
- "reply_message" phải là câu tiếng Việt ngắn gọn, tự nhiên, phù hợp để chuyển thành giọng nói.
- Ví dụ:
  - "Đã bật quạt."
  - "Đã tắt đèn."
  - "Đang kiểm tra môi trường."
  - "Đã bật đèn mức 50 phần trăm."

Chỉ trả về đúng một JSON object duy nhất.'''

_last_llm_request_at = 0.0
_llm_rate_limit_lock = threading.Lock()

IOT_INTENT_SYSTEM_PROMPT += '''

Additional multi-command requirement:
- Prefer this JSON shape for all responses:
  {
    "commands": [
      {
        "action": "turn_on" | "turn_off" | "get_status",
        "device": "light" | "fan" | "sensor",
        "value": 0-100 | null
      }
    ],
    "reply_message": "short Vietnamese reply for text-to-speech"
  }
- Return one command for each distinct user request in the same sentence.
- Example: "toi nong va troi toi qua" means two commands: turn_on fan and turn_on light.
- If the user asks for fan and light together, include both commands in commands.
- reply_message must summarize all commands in one short Vietnamese sentence.
'''


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


def parse_iot_intent(user_text: str) -> dict[str, Any]:
    response = chat_with_llm(user_text)
    parsed = parse_json_object(response.text)
    commands = parse_intent_commands(parsed)
    reply_message = parsed.get('reply_message')

    if not isinstance(reply_message, str) or not reply_message.strip():
        raise LLMIntentParseError('reply_message must be a non-empty string.')

    intent = {
        'commands': commands,
        'reply_message': reply_message.strip(),
    }

    if len(commands) == 1:
        intent.update(commands[0])

    return intent


def parse_intent_commands(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    raw_commands = parsed.get('commands')
    if raw_commands is None:
        raw_commands = [
            {
                'action': parsed.get('action'),
                'device': parsed.get('device'),
                'value': parsed.get('value'),
            }
        ]

    if not isinstance(raw_commands, list) or not raw_commands:
        raise LLMIntentParseError('commands must be a non-empty list.')

    commands = []
    seen = set()
    for raw_command in raw_commands:
        if not isinstance(raw_command, dict):
            raise LLMIntentParseError('Each command must be an object.')

        command = parse_intent_command(raw_command)
        dedupe_key = (command['action'], command['device'])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        commands.append(command)

    if not commands:
        raise LLMIntentParseError('commands must contain at least one valid command.')

    return commands


def parse_intent_command(raw_command: dict[str, Any]) -> dict[str, Any]:
    action = raw_command.get('action')
    device = raw_command.get('device')
    value = parse_output_value(raw_command.get('value'))

    if action not in ALLOWED_ACTIONS:
        raise LLMIntentParseError(f'Invalid action: {action!r}')

    if device not in ALLOWED_DEVICES:
        raise LLMIntentParseError(f'Invalid device: {device!r}')

    return {
        'action': action,
        'device': device,
        'value': value,
    }


def parse_output_value(value: Any) -> int | None:
    if value is None or value == '':
        return None

    if isinstance(value, bool):
        raise LLMIntentParseError('value must be a number from 0 to 100 or null.')

    if isinstance(value, str):
        value = value.strip().rstrip('%').strip()

    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LLMIntentParseError('value must be a number from 0 to 100 or null.') from exc

    if parsed < 0 or parsed > 100:
        raise LLMIntentParseError('value must be between 0 and 100.')

    return round(parsed)


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
            max_tokens=180,
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
