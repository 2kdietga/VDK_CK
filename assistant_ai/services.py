from __future__ import annotations

import json
import os
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from groq import Groq


DEFAULT_GROQ_MODEL = 'llama-3.1-8b-instant'
ALLOWED_ACTIONS = {'turn_on', 'turn_off', 'get_status'}
ALLOWED_DEVICES = {'light', 'fan', 'sensor'}
ALLOWED_AUTOMATION_OPERATIONS = {'create', 'update', 'upsert', 'enable', 'disable', 'delete'}
ALLOWED_AUTOMATION_FIELDS = {'temperature', 'humidity', 'light'}
ALLOWED_AUTOMATION_TARGETS = {'led', 'fan', 'light'}
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
- If CURRENT_SYSTEM_CONTEXT is provided and the user asks about temperature, humidity, light level, fan, or LED state, use those current values in reply_message.
- Use latest_sensor.temperature for temperature, latest_sensor.humidity for humidity, latest_sensor.light for light level.
- Use outputs.fan.state/value and outputs.led.state/value for fan and LED state.
- If the requested current value is missing, say you do not have that data yet. Do not invent values.
- If the user asks to create, change, enable, disable, or delete an automation rule, include automation_rules.
- For range rules such as "tren 30 va duoi 50", put both limits in automation_rules[].conditions. Conditions are AND.
- Automation JSON shape:
  {
    "automation_rules": [
      {
        "operation": "create" | "update" | "upsert" | "enable" | "disable" | "delete",
        "name": "short rule name or null",
        "condition": {"field": "temperature" | "humidity" | "light", "operator": ">" | ">=" | "<" | "<=" | "==" | "!=", "value": number},
        "conditions": [{"field": "temperature" | "humidity" | "light", "operator": ">" | ">=" | "<" | "<=" | "==" | "!=", "value": number}],
        "action": {"target": "fan" | "led" | "light", "state": true | false, "value": 0-100 | null, "cooldown_seconds": integer | null}
      }
    ]
  }
- Example: "khi nhiet do tren 30 thi bat quat 80 phan tram" means create one automation rule with temperature > 30 and fan on value 80.
- Example: "khi nhiet do tren 30 va duoi 50 thi bat quat" means create one automation rule with conditions temperature > 30 AND temperature < 50.
- Example: "khi troi toi duoi 300 thi bat den" means create one automation rule with light < 300 and led on.
- For automation-only requests, commands may be an empty list.

QUY TẮC RẤT QUAN TRỌNG VỀ ACTION CẤP NGOÀI:

Trường "action" ở cấp JSON ngoài cùng chỉ được nhận một trong ba giá trị:
- "turn_on"
- "turn_off"
- "get_status"

Tuyệt đối không được đặt:
- "action": "create"
- "action": "update"
- "action": "delete"
- "action": "enable"
- "action": "disable"
- "action": "upsert"

Các giá trị create, update, upsert, enable, disable, delete chỉ được dùng trong:
automation_rules[].operation

Nếu người dùng yêu cầu tạo, sửa, bật, tắt hoặc xóa luật tự động hóa, thì JSON cấp ngoài phải dùng:
{
  "action": "get_status",
  "device": "sensor",
  "value": null
}

Sau đó mới thêm automation_rules.

Ví dụ đúng:
{
  "action": "get_status",
  "device": "sensor",
  "value": null,
  "reply_message": "Đã tạo luật tự động bật quạt khi nhiệt độ trên 30 độ.",
  "automation_rules": [
    {
      "operation": "create",
      "name": "bat_quat_khi_nong",
      "condition": {
        "field": "temperature",
        "operator": ">",
        "value": 30
      },
      "action": {
        "target": "fan",
        "state": true,
        "value": 80,
        "cooldown_seconds": null
      }
    }
  ]
}

Ví dụ sai, tuyệt đối không được trả:
{
  "action": "create",
  "device": "fan",
  "value": 80
'''

IOT_INTENT_SYSTEM_PROMPT += '''

FINAL OUTPUT CONTRACT - HIGHEST PRIORITY:

Always return exactly this top-level shape:
{
  "commands": [],
  "automation_rules": [],
  "reply_message": "short Vietnamese reply"
}

Use commands only for immediate device control.
Each item in commands may use:
{
  "action": "turn_on" | "turn_off" | "get_status",
  "device": "light" | "fan" | "sensor",
  "value": 0-100 | null
}

Use automation_rules only for automation/rule requests.
Each item in automation_rules must use:
{
  "operation": "create" | "update" | "upsert" | "enable" | "disable" | "delete",
  "name": "short rule name or null",
  "condition": {"field": "temperature" | "humidity" | "light", "operator": ">" | ">=" | "<" | "<=" | "==" | "!=", "value": number},
  "conditions": [{"field": "temperature" | "humidity" | "light", "operator": ">" | ">=" | "<" | "<=" | "==" | "!=", "value": number}],
  "action": {"target": "fan" | "led" | "light", "state": true | false, "value": 0-100 | null, "cooldown_seconds": integer | null}
}

Never use create/update/upsert/enable/disable/delete as a top-level action.
Top-level "action" is obsolete. Prefer commands[] and automation_rules[].

Correct automation example:
User: "khi nhiet do tren 30 thi bat quat 80 phan tram"
Return:
{
  "commands": [],
  "automation_rules": [
    {
      "operation": "create",
      "name": "Bat quat khi nhiet do tren 30",
      "condition": {"field": "temperature", "operator": ">", "value": 30},
      "action": {"target": "fan", "state": true, "value": 80, "cooldown_seconds": 60}
    }
  ],
  "reply_message": "Đã tạo rule bật quạt 80 phần trăm khi nhiệt độ trên 30 độ."
}

Correct range automation example:
User: "khi nhiet do tren 30 va duoi 50 thi bat quat"
Return:
{
  "commands": [],
  "automation_rules": [
    {
      "operation": "create",
      "name": "Bat quat khi nhiet do tu 30 den 50",
      "conditions": [
        {"field": "temperature", "operator": ">", "value": 30},
        {"field": "temperature", "operator": "<", "value": 50}
      ],
      "action": {"target": "fan", "state": true, "value": 100, "cooldown_seconds": 60}
    }
  ],
  "reply_message": "Đã tạo rule bật quạt khi nhiệt độ trên 30 và dưới 50 độ."
}

Correct immediate-control example:
User: "bat quat 80 phan tram"
Return:
{
  "commands": [
    {"action": "turn_on", "device": "fan", "value": 80}
  ],
  "automation_rules": [],
  "reply_message": "Đã bật quạt 80 phần trăm."
}
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


def chat_with_llm(message: str, context: dict[str, Any] | None = None) -> LLMResponse:
    provider = os.environ.get('LLM_PROVIDER', 'groq').lower()

    if provider == 'groq':
        return chat_with_groq(message, context=context)

    raise LLMConfigurationError(f'Unsupported LLM_PROVIDER: {provider}')


def parse_iot_intent(user_text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    response = chat_with_llm(user_text, context=context)
    parsed = normalize_llm_schema(parse_json_object(response.text))
    automation_rules = parse_automation_rule_requests(parsed.get('automation_rules'))
    commands = parse_intent_commands(parsed, allow_empty=bool(automation_rules))
    if automation_rules and is_automation_request(user_text):
        commands = []
    reply_message = parsed.get('reply_message')

    if not isinstance(reply_message, str) or not reply_message.strip():
        raise LLMIntentParseError('reply_message must be a non-empty string.')

    context_reply = build_context_reply(user_text, context)
    if context_reply:
        reply_message = context_reply

    intent = {
        'commands': commands,
        'automation_rules': automation_rules,
        'reply_message': reply_message.strip(),
    }

    if len(commands) == 1 and not automation_rules:
        intent.update(commands[0])

    return intent


def normalize_llm_schema(parsed: dict[str, Any]) -> dict[str, Any]:
    if parsed.get('automation_rules') is not None:
        return parsed

    if parsed.get('action') not in ALLOWED_AUTOMATION_OPERATIONS:
        return parsed

    automation_rule = {
        'operation': parsed.get('operation') or parsed.get('action'),
        'name': parsed.get('name') or parsed.get('rule_name'),
        'description': parsed.get('description', ''),
        'is_enabled': parsed.get('is_enabled', True),
    }

    conditions = parsed.get('conditions')
    condition = parsed.get('condition')
    if conditions is None and condition is not None:
        conditions = condition
    if conditions is None:
        conditions = build_automation_condition_from_flat_payload(parsed)

    rule_action = parsed.get('rule_action') or parsed.get('then') or parsed.get('output_action')
    if rule_action is None:
        rule_action = build_automation_action_from_flat_payload(parsed)

    if conditions is not None:
        automation_rule['conditions'] = conditions
    if rule_action is not None:
        automation_rule['action'] = rule_action

    return {
        **parsed,
        'commands': parsed.get('commands', []),
        'automation_rules': [automation_rule],
    }


def build_automation_condition_from_flat_payload(parsed: dict[str, Any]) -> dict[str, Any] | None:
    field = parsed.get('field') or parsed.get('sensor') or parsed.get('condition_field')
    operator_name = parsed.get('operator') or parsed.get('condition_operator')
    value = parsed.get('threshold') or parsed.get('condition_value')

    if field is None or operator_name is None or value is None:
        return None

    return {
        'field': field,
        'operator': operator_name,
        'value': value,
    }


def build_automation_action_from_flat_payload(parsed: dict[str, Any]) -> dict[str, Any] | None:
    target = parsed.get('target') or parsed.get('device')
    state = parsed.get('state')
    if state is None:
        desired_action = parsed.get('device_action') or parsed.get('output_state')
        if desired_action in {'turn_on', 'on', 'bat'}:
            state = True
        elif desired_action in {'turn_off', 'off', 'tat'}:
            state = False

    if target is None or state is None:
        return None

    return {
        'target': target,
        'state': state,
        'value': parsed.get('value'),
        'cooldown_seconds': parsed.get('cooldown_seconds'),
    }


def parse_intent_commands(parsed: dict[str, Any], allow_empty: bool = False) -> list[dict[str, Any]]:
    raw_commands = parsed.get('commands')
    if raw_commands is None:
        if allow_empty and 'action' not in parsed:
            return []
        raw_commands = [
            {
                'action': parsed.get('action'),
                'device': parsed.get('device'),
                'value': parsed.get('value'),
            }
        ]

    if not isinstance(raw_commands, list) or not raw_commands:
        if allow_empty:
            return []
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


def parse_automation_rule_requests(raw_requests: Any) -> list[dict[str, Any]]:
    if raw_requests is None:
        return []

    if isinstance(raw_requests, dict):
        raw_requests = [raw_requests]

    if not isinstance(raw_requests, list):
        raise LLMIntentParseError('automation_rules must be a list.')

    return [parse_automation_rule_request(raw_request) for raw_request in raw_requests]


def parse_automation_rule_request(raw_request: Any) -> dict[str, Any]:
    if not isinstance(raw_request, dict):
        raise LLMIntentParseError('Each automation rule request must be an object.')

    operation = raw_request.get('operation', 'upsert')
    if operation not in ALLOWED_AUTOMATION_OPERATIONS:
        raise LLMIntentParseError(f'Invalid automation operation: {operation!r}')

    request = {
        'operation': operation,
        'name': raw_request.get('name'),
        'description': raw_request.get('description', ''),
        'is_enabled': raw_request.get('is_enabled', True),
    }

    if operation in {'create', 'update', 'upsert'}:
        request['conditions'] = parse_automation_conditions(raw_request)
        request['condition'] = request['conditions'][0]
        request['action'] = parse_automation_action(raw_request.get('action'))

    return request


def parse_automation_conditions(raw_request: dict[str, Any]) -> list[dict[str, Any]]:
    raw_conditions = raw_request.get('conditions')
    if raw_conditions is None:
        raw_conditions = raw_request.get('condition')

    if isinstance(raw_conditions, dict):
        raw_conditions = [raw_conditions]

    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise LLMIntentParseError('automation conditions must be a non-empty list.')

    return [parse_automation_condition(raw_condition) for raw_condition in raw_conditions]


def parse_automation_condition(raw_condition: Any) -> dict[str, Any]:
    if not isinstance(raw_condition, dict):
        raise LLMIntentParseError('automation condition must be an object.')

    field = raw_condition.get('field')
    operator_name = raw_condition.get('operator')
    if field not in ALLOWED_AUTOMATION_FIELDS:
        raise LLMIntentParseError(f'Invalid automation field: {field!r}')

    if operator_name not in {'>', '>=', '<', '<=', '==', '!='}:
        raise LLMIntentParseError(f'Invalid automation operator: {operator_name!r}')

    try:
        value = float(raw_condition.get('value'))
    except (TypeError, ValueError) as exc:
        raise LLMIntentParseError('automation condition value must be a number.') from exc

    return {
        'field': field,
        'operator': operator_name,
        'value': value,
    }


def parse_automation_action(raw_action: Any) -> dict[str, Any]:
    if not isinstance(raw_action, dict):
        raise LLMIntentParseError('automation action must be an object.')

    target = raw_action.get('target')
    if target not in ALLOWED_AUTOMATION_TARGETS:
        raise LLMIntentParseError(f'Invalid automation target: {target!r}')

    state = raw_action.get('state')
    if not isinstance(state, bool):
        raise LLMIntentParseError('automation action state must be true or false.')

    value = parse_output_value(raw_action.get('value'))
    if value is None:
        value = 100 if state else 0

    cooldown_seconds = raw_action.get('cooldown_seconds')
    if cooldown_seconds is None:
        cooldown_seconds = 60
    if isinstance(cooldown_seconds, bool):
        raise LLMIntentParseError('automation cooldown_seconds must be a number.')

    try:
        cooldown_seconds = int(cooldown_seconds)
    except (TypeError, ValueError) as exc:
        raise LLMIntentParseError('automation cooldown_seconds must be a number.') from exc

    return {
        'target': target,
        'state': state,
        'value': value,
        'cooldown_seconds': max(0, cooldown_seconds),
    }


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


def build_context_reply(user_text: str, context: dict[str, Any] | None = None) -> str | None:
    if not context:
        return None

    normalized = normalize_vietnamese_text(user_text)
    if not is_information_question(normalized):
        return None

    latest_sensor = context.get('latest_sensor')
    outputs = context.get('outputs') if isinstance(context.get('outputs'), dict) else {}

    if 'nhiet do' in normalized:
        value = get_sensor_value(latest_sensor, 'temperature')
        return format_measurement_reply('Nhiệt độ hiện tại', value, 'độ C')

    if 'do am' in normalized or 'am do' in normalized:
        value = get_sensor_value(latest_sensor, 'humidity')
        return format_measurement_reply('Độ ẩm hiện tại', value, '%')

    if 'anh sang' in normalized or 'cuong do sang' in normalized or 'do sang' in normalized:
        value = get_sensor_value(latest_sensor, 'light')
        return format_measurement_reply('Cường độ ánh sáng hiện tại', value, '')

    if 'quat' in normalized:
        return format_output_reply('Quạt', outputs.get('fan'))

    if 'den' in normalized or 'led' in normalized:
        return format_output_reply('Đèn', outputs.get('led'))

    if 'thong so' in normalized or 'moi truong' in normalized or 'trang thai phong' in normalized:
        return format_environment_reply(latest_sensor)

    return None


def normalize_vietnamese_text(text: str) -> str:
    normalized = unicodedata.normalize('NFD', text.lower())
    return ''.join(char for char in normalized if unicodedata.category(char) != 'Mn').replace('đ', 'd')


def is_information_question(normalized_text: str) -> bool:
    question_markers = [
        'bao nhieu',
        'may',
        'hien tai',
        'bay gio',
        'dang',
        'cho biet',
        'kiem tra',
        'xem',
        'thong tin',
        'thong so',
        'trang thai',
    ]
    return any(marker in normalized_text for marker in question_markers)


def is_automation_request(text: str) -> bool:
    normalized_text = normalize_vietnamese_text(text)
    markers = [
        'khi ',
        'neu ',
        'rule',
        'automation',
        'tu dong',
        'luat',
        'dieu kien',
    ]
    return any(marker in normalized_text for marker in markers)


def get_sensor_value(latest_sensor: Any, key: str) -> Any:
    if not isinstance(latest_sensor, dict):
        return None
    return latest_sensor.get(key)


def format_measurement_reply(label: str, value: Any, unit: str) -> str:
    if value is None:
        return f'Tôi chưa có dữ liệu {label.lower()}.'

    formatted_value = format_number(value)
    if unit == '%':
        return f'{label} là {formatted_value}%.'

    if unit:
        return f'{label} là {formatted_value} {unit}.'

    return f'{label} là {formatted_value}.'


def format_output_reply(label: str, output: Any) -> str:
    if not isinstance(output, dict):
        return f'Tôi chưa có dữ liệu trạng thái {label.lower()}.'

    state = output.get('state')
    value = output.get('value')
    if state is None:
        return f'Tôi chưa có dữ liệu trạng thái {label.lower()}.'

    state_text = 'bật' if state else 'tắt'
    if value is None:
        return f'{label} hiện đang {state_text}.'

    return f'{label} hiện đang {state_text}, mức {format_number(value)} phần trăm.'


def format_environment_reply(latest_sensor: Any) -> str:
    if not isinstance(latest_sensor, dict):
        return 'Tôi chưa có dữ liệu môi trường hiện tại.'

    temperature = latest_sensor.get('temperature')
    humidity = latest_sensor.get('humidity')
    light = latest_sensor.get('light')
    parts = []
    if temperature is not None:
        parts.append(f'nhiệt độ {format_number(temperature)} độ C')
    if humidity is not None:
        parts.append(f'độ ẩm {format_number(humidity)}%')
    if light is not None:
        parts.append(f'ánh sáng {format_number(light)}')

    if not parts:
        return 'Tôi chưa có dữ liệu môi trường hiện tại.'

    return 'Hiện tại ' + ', '.join(parts) + '.'


def format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    if number.is_integer():
        return str(round(number))

    return f'{number:.1f}'.rstrip('0').rstrip('.')


def chat_with_groq(message: str, context: dict[str, Any] | None = None) -> LLMResponse:
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
                    'content': build_llm_user_content(message, context),
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


def build_llm_user_content(message: str, context: dict[str, Any] | None = None) -> str:
    if not context:
        return message

    return (
        f'CURRENT_SYSTEM_CONTEXT:\n{json.dumps(context, ensure_ascii=False)}\n\n'
        f'USER_TRANSCRIPT:\n{message}'
    )


def get_current_iot_context() -> dict[str, Any]:
    from control.models import OutputTarget
    from gateway.protocol import get_esp32_state
    from monitoring.models import SensorReading

    state = get_esp32_state()
    latest_sensor = state.latest_sensor
    sensor_source = 'ram'

    if latest_sensor is None:
        latest_reading = SensorReading.objects.order_by('-created_at').first()
        if latest_reading is not None:
            latest_sensor = {
                'timestamp': latest_reading.created_at.isoformat(),
                'temperature': latest_reading.temperature,
                'humidity': latest_reading.humidity,
                'light': latest_reading.light,
            }
            sensor_source = 'database'

    outputs = {}
    for output in OutputTarget.objects.filter(key__in=['led', 'fan']):
        current_state = output.current_state if isinstance(output.current_state, dict) else {}
        outputs[output.key] = {
            'name': output.name,
            'kind': output.kind,
            'is_enabled': output.is_enabled,
            'state': current_state.get('state'),
            'value': current_state.get('value'),
            'updated_at': output.updated_at.isoformat(),
        }

    return {
        'esp32_connected': state.connected,
        'last_seen': state.last_seen,
        'latest_sensor': latest_sensor,
        'sensor_source': sensor_source if latest_sensor is not None else None,
        'outputs': outputs,
    }


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
