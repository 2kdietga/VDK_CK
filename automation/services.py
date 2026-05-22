from __future__ import annotations

import operator
import uuid
from datetime import timedelta
from typing import Any

from django.utils import timezone

from control.models import CommandLog, OutputTarget

from .models import AutomationRule


DEFAULT_RULE_COOLDOWN_SECONDS = 60
ALLOWED_RULE_FIELDS = {'temperature', 'humidity', 'light'}
ALLOWED_RULE_TARGETS = {'led', 'fan'}
FIELD_ALIASES = {
    'temp': 'temperature',
    'nhiet_do': 'temperature',
    'humidity_percent': 'humidity',
    'do_am': 'humidity',
    'lux': 'light',
    'light_level': 'light',
    'anh_sang': 'light',
}
TARGET_ALIASES = {
    'light': 'led',
    'den': 'led',
    'led': 'led',
    'fan': 'fan',
    'quat': 'fan',
}
OPERATORS = {
    '>': operator.gt,
    '>=': operator.ge,
    '<': operator.lt,
    '<=': operator.le,
    '==': operator.eq,
    '=': operator.eq,
    '!=': operator.ne,
}


def evaluate_automation_rules(sensor_data: dict[str, Any]) -> list[dict[str, Any]]:
    commands = []
    for rule in AutomationRule.objects.filter(is_enabled=True):
        if not rule_matches(rule, sensor_data):
            continue

        command = build_rule_command(rule)
        if command is not None:
            commands.append(command)

    return commands


def apply_automation_rule_requests(requests: list[dict[str, Any]]) -> list[str]:
    results = []
    for request in requests:
        result = apply_automation_rule_request(request)
        if result:
            results.append(result)
    return results


def apply_automation_rule_request(request: dict[str, Any]) -> str:
    operation = request.get('operation', 'upsert')
    name = str(request.get('name') or '').strip()

    if operation in {'create', 'update', 'upsert'}:
        rule_data = build_rule_data_from_request(request)
        if rule_data is None:
            return ''

        if not name:
            name = default_rule_name(rule_data['conditions'][0], rule_data['action'])

        conflicts = find_conflicting_rules(name, rule_data)
        disabled_conflict_names = list(conflicts.values_list('name', flat=True))
        conflicts.update(is_enabled=False, updated_at=timezone.now())

        rule, created = AutomationRule.objects.update_or_create(
            name=name,
            defaults={
                'description': request.get('description', ''),
                'conditions': rule_data['conditions'],
                'action': rule_data['action'],
                'is_enabled': request.get('is_enabled', True),
            },
        )
        result = f'{"Created" if created else "Updated"} automation rule: {rule.name}.'
        if disabled_conflict_names:
            result += f' Disabled conflicting rules: {", ".join(disabled_conflict_names)}.'
        return result

    if not name:
        return ''

    rule = AutomationRule.objects.filter(name=name).first()
    if rule is None:
        return f'Automation rule not found: {name}.'

    if operation == 'enable':
        rule.is_enabled = True
        rule.save(update_fields=['is_enabled', 'updated_at'])
        return f'Enabled automation rule: {rule.name}.'

    if operation == 'disable':
        rule.is_enabled = False
        rule.save(update_fields=['is_enabled', 'updated_at'])
        return f'Disabled automation rule: {rule.name}.'

    if operation == 'delete':
        rule.delete()
        return f'Deleted automation rule: {name}.'

    return ''


def build_rule_data_from_request(request: dict[str, Any]) -> dict[str, Any] | None:
    conditions = normalize_rule_conditions(request)
    action = request.get('action')
    if not conditions or not isinstance(action, dict):
        return None

    target = normalize_target(action.get('target'))
    state = action.get('state')
    if target not in ALLOWED_RULE_TARGETS or not isinstance(state, bool):
        return None

    value = action.get('value', 100 if state else 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > 100:
        return None

    cooldown_seconds = parse_cooldown_seconds(action.get('cooldown_seconds'))
    return {
        'conditions': conditions,
        'action': {
            'name': 'set_output',
            'params': {
                'target': target,
                'state': state,
                'value': round(value),
            },
            'cooldown_seconds': cooldown_seconds,
        },
    }


def normalize_rule_conditions(request: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw_conditions = request.get('conditions')
    if raw_conditions is None:
        raw_conditions = request.get('condition')

    if isinstance(raw_conditions, dict):
        raw_conditions = [raw_conditions]

    if not isinstance(raw_conditions, list) or not raw_conditions:
        return None

    conditions = []
    for condition in raw_conditions:
        if not isinstance(condition, dict):
            return None

        field = normalize_field(condition.get('field'))
        operator_name = condition.get('operator')
        if field not in ALLOWED_RULE_FIELDS or operator_name not in OPERATORS:
            return None

        try:
            threshold = float(condition.get('value'))
        except (TypeError, ValueError):
            return None

        conditions.append(
            {
                'field': field,
                'operator': operator_name,
                'value': threshold,
            }
        )

    return conditions


def normalize_target(target: Any) -> str | None:
    if not isinstance(target, str) or not target:
        return None

    normalized = target.strip()
    return TARGET_ALIASES.get(normalized, normalized)


def default_rule_name(condition: dict[str, Any], action: dict[str, Any]) -> str:
    params = action.get('params', {})
    state = 'on' if params.get('state') else 'off'
    return (
        f"Auto {condition.get('field')} {condition.get('operator')} {condition.get('value')} "
        f"-> {params.get('target')} {state}"
    )


def find_conflicting_rules(rule_name: str, rule_data: dict[str, Any]):
    new_conditions = normalized_conditions(rule_data.get('conditions'))
    new_params = action_params(rule_data.get('action'))
    if not new_conditions or not new_params:
        return AutomationRule.objects.none()

    candidates = AutomationRule.objects.filter(is_enabled=True).exclude(name=rule_name)
    conflict_ids = []
    for rule in candidates:
        existing_conditions = normalized_conditions(rule.conditions)
        existing_params = action_params(rule.action)
        if rules_conflict(new_conditions, new_params, existing_conditions, existing_params):
            conflict_ids.append(rule.id)

    return AutomationRule.objects.filter(id__in=conflict_ids)


def rules_conflict(
    new_conditions: list[dict[str, Any]],
    new_params: dict[str, Any],
    existing_conditions: list[dict[str, Any]],
    existing_params: dict[str, Any] | None,
) -> bool:
    if not existing_conditions or not existing_params:
        return False

    if new_params.get('target') != existing_params.get('target'):
        return False

    if new_params == existing_params:
        return False

    new_range = conditions_to_single_field_range(new_conditions)
    existing_range = conditions_to_single_field_range(existing_conditions)
    if new_range is None or existing_range is None:
        return True

    new_field, new_min, new_max = new_range
    existing_field, existing_min, existing_max = existing_range
    if new_field != existing_field:
        return False

    return new_min <= existing_max and existing_min <= new_max


def first_condition(conditions: Any) -> dict[str, Any] | None:
    if isinstance(conditions, list) and conditions and isinstance(conditions[0], dict):
        return conditions[0]
    return None


def normalized_conditions(conditions: Any) -> list[dict[str, Any]]:
    if not isinstance(conditions, list):
        return []

    normalized = []
    for condition in conditions:
        if isinstance(condition, dict):
            normalized.append(condition)
    return normalized


def action_params(action: Any) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None

    params = action.get('params')
    if not isinstance(params, dict):
        return None

    return normalize_set_output_params(params)


def condition_ranges_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_range = condition_to_range(left)
    right_range = condition_to_range(right)
    if left_range is None or right_range is None:
        return True

    left_min, left_max = left_range
    right_min, right_max = right_range
    return left_min <= right_max and right_min <= left_max


def conditions_to_single_field_range(conditions: list[dict[str, Any]]) -> tuple[str, float, float] | None:
    field = None
    min_value = float('-inf')
    max_value = float('inf')

    for condition in conditions:
        condition_field = normalize_field(condition.get('field'))
        if condition_field is None:
            return None
        if field is None:
            field = condition_field
        elif field != condition_field:
            return None

        condition_range = condition_to_range(condition)
        if condition_range is None:
            return None

        condition_min, condition_max = condition_range
        min_value = max(min_value, condition_min)
        max_value = min(max_value, condition_max)

    if field is None:
        return None

    return field, min_value, max_value


def condition_to_range(condition: dict[str, Any]) -> tuple[float, float] | None:
    operator_name = condition.get('operator')
    try:
        value = float(condition.get('value'))
    except (TypeError, ValueError):
        return None

    if operator_name in {'>', '>='}:
        return (value, float('inf'))
    if operator_name in {'<', '<='}:
        return (float('-inf'), value)
    if operator_name in {'=', '=='}:
        return (value, value)
    if operator_name == '!=':
        return (float('-inf'), float('inf'))
    return None


def rule_matches(rule: AutomationRule, sensor_data: dict[str, Any]) -> bool:
    conditions = rule.conditions
    if not isinstance(conditions, list) or not conditions:
        return False

    return all(condition_matches(condition, sensor_data) for condition in conditions)


def condition_matches(condition: Any, sensor_data: dict[str, Any]) -> bool:
    if not isinstance(condition, dict):
        return False

    field = normalize_field(condition.get('field'))
    operator_name = condition.get('operator')
    expected = condition.get('value')

    if field is None or operator_name not in OPERATORS:
        return False

    actual = sensor_data.get(field)
    if actual is None or expected is None:
        return False

    try:
        actual_value = float(actual)
        expected_value = float(expected)
    except (TypeError, ValueError):
        return False

    return OPERATORS[operator_name](actual_value, expected_value)


def normalize_field(field: Any) -> str | None:
    if not isinstance(field, str) or not field:
        return None

    normalized = field.strip()
    return FIELD_ALIASES.get(normalized, normalized)


def build_rule_command(rule: AutomationRule) -> dict[str, Any] | None:
    action = rule.action
    if not isinstance(action, dict):
        return None

    name = action.get('name')
    params = action.get('params', {})
    if name != 'set_output' or not isinstance(params, dict):
        return None

    params = normalize_set_output_params(params)
    if params is None:
        return None

    if output_already_matches(params):
        return None

    cooldown_seconds = parse_cooldown_seconds(action.get('cooldown_seconds'))
    if matching_recent_command_exists(params, cooldown_seconds):
        return None

    command = {
        'type': 'server.command',
        'command_id': uuid.uuid4().hex,
        'name': name,
        'params': params,
    }
    CommandLog.objects.create(
        command_id=command['command_id'],
        name=name,
        target=params['target'],
        params=params,
        source=CommandLog.Source.RULE,
        status=CommandLog.Status.SENT,
        sent_at=timezone.now(),
    )
    return command


def normalize_set_output_params(params: dict[str, Any]) -> dict[str, Any] | None:
    target = params.get('target')
    if target not in {'led', 'fan'}:
        return None

    state = params.get('state')
    if not isinstance(state, bool):
        return None

    value = params.get('value', 100 if state else 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    if value < 0 or value > 100:
        return None

    return {
        'target': target,
        'state': state,
        'value': round(value),
    }


def output_already_matches(params: dict[str, Any]) -> bool:
    output = OutputTarget.objects.filter(key=params['target'], is_enabled=True).first()
    if output is None or not isinstance(output.current_state, dict):
        return False

    current_state = output.current_state
    return (
        current_state.get('state') == params['state']
        and current_state.get('value') == params['value']
    )


def matching_recent_command_exists(params: dict[str, Any], cooldown_seconds: int) -> bool:
    if cooldown_seconds <= 0:
        return False

    cutoff = timezone.now() - timedelta(seconds=cooldown_seconds)
    return CommandLog.objects.filter(
        target=params['target'],
        params=params,
        source=CommandLog.Source.RULE,
        created_at__gte=cutoff,
        status__in=[CommandLog.Status.SENT, CommandLog.Status.COMPLETED],
    ).exists()


def parse_cooldown_seconds(value: Any) -> int:
    if value is None:
        return DEFAULT_RULE_COOLDOWN_SECONDS

    if isinstance(value, bool):
        return DEFAULT_RULE_COOLDOWN_SECONDS

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RULE_COOLDOWN_SECONDS

    return max(0, parsed)
