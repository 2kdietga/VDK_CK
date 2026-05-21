from __future__ import annotations

import operator
import uuid
from datetime import timedelta
from typing import Any

from django.utils import timezone

from control.models import CommandLog, OutputTarget

from .models import AutomationRule


DEFAULT_RULE_COOLDOWN_SECONDS = 60
FIELD_ALIASES = {
    'temp': 'temperature',
    'nhiet_do': 'temperature',
    'humidity_percent': 'humidity',
    'do_am': 'humidity',
    'lux': 'light',
    'light_level': 'light',
    'anh_sang': 'light',
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
