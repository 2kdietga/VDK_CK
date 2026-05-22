from __future__ import annotations

import json
import os
import re
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
IOT_INTENT_SYSTEM_PROMPT = '''
Bạn là trợ lý AIoT cho hệ thống giám sát môi trường ESP32.
Nhiệm vụ: phân tích câu nói tiếng Việt của người dùng và chỉ trả về đúng 1 JSON object, không markdown, không giải thích.

Luôn trả về đúng cấu trúc:
{
  "commands": [],
  "automation_rules": [],
  "reply_message": "câu phản hồi tiếng Việt ngắn gọn"
}

1. commands dùng cho lệnh điều khiển ngay:
{
  "action": "turn_on" | "turn_off" | "get_status",
  "device": "light" | "fan" | "sensor",
  "value": 0-100 | null
}

Quy tắc commands:
- Nóng, oi, bí, cần mát hơn → bật fan.
- Lạnh, mát rồi, không cần quạt → tắt fan.
- Tối, thiếu sáng, bật đèn, sáng hơn → bật light.
- Sáng quá, tắt đèn, không cần đèn → tắt light.
- Hỏi nhiệt độ, độ ẩm, ánh sáng, môi trường, trạng thái phòng → get_status sensor.
- Nếu có phần trăm/mức 0-100 thì đưa vào value.
- Nếu bật mà không nói mức → value null.
- Nếu tắt → value 0.
- Nếu có nhiều yêu cầu trong một câu, tạo nhiều command.

2. automation_rules dùng cho yêu cầu tạo/sửa/bật/tắt/xóa luật tự động:
{
  "operation": "create" | "update" | "upsert" | "enable" | "disable" | "delete",
  "name": "tên rule ngắn hoặc null",
  "condition": {"field": "temperature" | "humidity" | "light", "operator": ">" | ">=" | "<" | "<=" | "==" | "!=", "value": number},
  "conditions": [
    {"field": "temperature" | "humidity" | "light", "operator": ">" | ">=" | "<" | "<=" | "==" | "!=", "value": number}
  ],
  "action": {"target": "fan" | "led" | "light", "state": true | false, "value": 0-100 | null, "cooldown_seconds": integer | null}
}

Quy tắc automation_rules:
- Yêu cầu kiểu "khi... thì..." hoặc "nếu... thì..." là tạo rule tự động.
- Ví dụ: "khi nhiệt độ trên 30 thì bật quạt 80%" → create rule temperature > 30, fan on value 80.
- Nếu có khoảng điều kiện như "trên 30 và dưới 50", dùng conditions với nhiều điều kiện AND.
- Nếu chỉ tạo rule tự động, commands phải là [].

3. Dùng CURRENT_SYSTEM_CONTEXT nếu có:
- latest_sensor.temperature: nhiệt độ
- latest_sensor.humidity: độ ẩm
- latest_sensor.light: ánh sáng
- outputs.fan.state/value: trạng thái quạt
- outputs.led.state/value: trạng thái đèn
Nếu thiếu dữ liệu thì nói chưa có dữ liệu, không tự bịa.

4. reply_message:
- Viết tiếng Việt ngắn gọn, tự nhiên, phù hợp để đọc bằng giọng nói.
- Tóm tắt đúng các lệnh hoặc rule đã xử lý.

5. Câu giao tiếp thông thường:
- Nếu người dùng chỉ chào hỏi, cảm ơn, tạm biệt, hỏi xã giao hoặc nói chuyện không liên quan đến điều khiển thiết bị/cảm biến/rule tự động, thì không tạo command và không tạo automation rule.
- Trả lời lịch sự, ngắn gọn trong reply_message.

Ví dụ:
User: "xin chào"
Return:
{
  "commands": [],
  "automation_rules": [],
  "reply_message": "Xin chào, tôi có thể giúp gì cho bạn?"
}

User: "cảm ơn"
Return:
{
  "commands": [],
  "automation_rules": [],
  "reply_message": "Không có gì ạ."
}

Lưu ý quan trọng:
- Không bao giờ dùng create/update/delete/enable/disable/upsert làm action trong commands.
- Các giá trị đó chỉ được dùng trong automation_rules[].operation.
- Chỉ trả về đúng JSON object duy nhất.
'''

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


def chat_with_llm(message: str, context: dict[str, Any] | None = None) -> LLMResponse:
    provider = os.environ.get('LLM_PROVIDER', 'groq').lower()

    if provider == 'groq':
        return chat_with_groq(message, context=context)

    raise LLMConfigurationError(f'Unsupported LLM_PROVIDER: {provider}')


def parse_iot_intent(user_text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    response = chat_with_llm(user_text, context=context)
    parsed = normalize_llm_schema(parse_json_object(response.text))
    parsed = complete_automation_rule_from_text(parsed, user_text)
    automation_rules = parse_automation_rule_requests(parsed.get('automation_rules'))
    commands = parse_intent_commands(
        parsed,
        allow_empty=bool(automation_rules) or parsed.get('commands') == [],
    )
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


def complete_automation_rule_from_text(parsed: dict[str, Any], user_text: str) -> dict[str, Any]:
    if not is_automation_request(user_text):
        return parsed

    fallback_rule = build_automation_rule_from_text(user_text)
    if fallback_rule is None:
        return parsed

    raw_rules = parsed.get('automation_rules')
    if raw_rules in (None, []):
        return {
            **parsed,
            'commands': parsed.get('commands', []),
            'automation_rules': [fallback_rule],
        }

    if isinstance(raw_rules, dict):
        raw_rules = [raw_rules]

    if not isinstance(raw_rules, list):
        return parsed

    completed_rules = []
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            completed_rules.append(raw_rule)
            continue

        completed_rule = {
            **fallback_rule,
            **raw_rule,
        }
        if not completed_rule.get('conditions'):
            completed_rule['conditions'] = fallback_rule['conditions']
        if not completed_rule.get('condition'):
            completed_rule['condition'] = fallback_rule['condition']
        if not completed_rule.get('action'):
            completed_rule['action'] = fallback_rule['action']
        if not completed_rule.get('operation'):
            completed_rule['operation'] = fallback_rule['operation']
        if not completed_rule.get('name'):
            completed_rule['name'] = fallback_rule['name']

        completed_rules.append(completed_rule)

    return {
        **parsed,
        'commands': parsed.get('commands', []),
        'automation_rules': completed_rules,
    }


def build_automation_rule_from_text(user_text: str) -> dict[str, Any] | None:
    normalized = normalize_vietnamese_text(user_text)
    condition = build_condition_from_text(normalized)
    action = build_action_from_text(normalized)
    if condition is None or action is None:
        return None

    target_label = 'quat' if action['target'] == 'fan' else 'den'
    return {
        'operation': 'create',
        'name': f'Rule {target_label} tu dong',
        'description': '',
        'is_enabled': True,
        'condition': condition[0],
        'conditions': condition,
        'action': action,
    }


def build_condition_from_text(normalized_text: str) -> list[dict[str, Any]] | None:
    field = None
    if 'nhiet do' in normalized_text or 'nong' in normalized_text:
        field = 'temperature'
    elif 'do am' in normalized_text or 'am do' in normalized_text:
        field = 'humidity'
    elif 'anh sang' in normalized_text or 'do sang' in normalized_text or 'troi toi' in normalized_text:
        field = 'light'

    if field is None:
        return None

    conditions = []
    condition_patterns = [
        (r'(?:tren|lon hon|cao hon)\s+(\d+(?:[.,]\d+)?)', '>'),
        (r'(?:duoi|nho hon|be hon|thap hon)\s+(\d+(?:[.,]\d+)?)', '<'),
        (r'(?:bang|=)\s+(\d+(?:[.,]\d+)?)', '=='),
    ]
    for pattern, operator_name in condition_patterns:
        for match in re.finditer(pattern, normalized_text):
            conditions.append(
                {
                    'field': field,
                    'operator': operator_name,
                    'value': parse_text_number(match.group(1)),
                }
            )

    return conditions or None


def build_action_from_text(normalized_text: str) -> dict[str, Any] | None:
    then_text = normalized_text.split(' thi ', 1)[1] if ' thi ' in normalized_text else normalized_text

    if 'quat' in then_text or 'fan' in then_text:
        target = 'fan'
    elif 'den' in then_text or 'led' in then_text:
        target = 'led'
    else:
        return None

    if 'tat' in then_text:
        state = False
    elif 'bat' in then_text or 'mo' in then_text:
        state = True
    else:
        return None

    value = parse_action_value_from_text(then_text)
    if value is None:
        value = 100 if state else 0

    return {
        'target': target,
        'state': state,
        'value': value,
        'cooldown_seconds': 60,
    }


def parse_action_value_from_text(text: str) -> int | None:
    match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:%|phan tram)?', text)
    if match is None:
        return None

    return parse_output_value(match.group(1))


def parse_text_number(value: str) -> float:
    return float(value.replace(',', '.'))


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
