from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from automation.models import AutomationRule
from control.models import CommandLog, OutputTarget
from gateway.protocol import get_esp32_state
from monitoring.models import SensorReading


@require_GET
def overview(request):
    esp32 = get_esp32_state().as_dict()
    context = {
        'esp32': esp32,
        'latest_sensor': esp32.get('latest_sensor') or {},
        'readings_count': SensorReading.objects.count(),
        'outputs_count': OutputTarget.objects.count(),
        'commands_count': CommandLog.objects.count(),
        'enabled_rules_count': AutomationRule.objects.filter(is_enabled=True).count(),
        'outputs': OutputTarget.objects.all()[:8],
        'command_rows': build_command_rows(CommandLog.objects.all()[:8]),
        'realtime_chart_data': esp32.get('realtime_sensor_samples') or [],
    }
    return render(request, 'dashboard/overview.html', context)


@require_GET
def sensors(request):
    readings = list(SensorReading.objects.all()[:300])
    chart_readings = list(reversed(readings))
    context = {
        'readings': readings[:100],
        'history_chart_data': [
            {
                'timestamp': reading.created_at.isoformat(),
                'temperature': reading.temperature,
                'humidity': reading.humidity,
                'light': reading.light,
                'sample_count': reading.raw_data.get('sample_count'),
            }
            for reading in chart_readings
        ],
    }
    return render(request, 'dashboard/sensors.html', context)


@require_GET
def controls(request):
    outputs = list(OutputTarget.objects.all())
    context = {
        'outputs': outputs,
        'controls_data': [
            {
                'key': output.key,
                'name': output.name,
                'kind': output.kind,
                'is_enabled': output.is_enabled,
                'current_state': output.current_state,
                'updated_at': output.updated_at.isoformat(),
            }
            for output in outputs
        ],
    }
    return render(request, 'dashboard/controls.html', context)


@require_GET
def commands(request):
    context = {
        'command_rows': build_command_rows(CommandLog.objects.all()[:100]),
    }
    return render(request, 'dashboard/commands.html', context)


@require_http_methods(['GET', 'POST'])
def rules(request):
    if request.method == 'POST':
        handle_rule_form(request)
        return redirect('dashboard:rules')

    context = {
        'rule_rows': build_rule_rows(AutomationRule.objects.all()),
        'field_options': rule_field_options(),
        'operator_options': rule_operator_options(),
        'target_options': rule_target_options(),
        'state_options': rule_state_options(),
    }
    return render(request, 'dashboard/rules.html', context)


def home(request):
    return redirect('dashboard:overview')


def build_command_rows(commands):
    return [
        {
            'command': command,
            'short_id': command.command_id[:8],
            'source_label': command.get_source_display(),
            'target_label': command.target.upper() if command.target else 'System',
            'state_label': format_command_state(command.params),
            'value_label': format_command_value(command.params),
            'status_class': command_status_class(command.status),
        }
        for command in commands
    ]


def format_command_state(params):
    if not isinstance(params, dict) or 'state' not in params:
        return '--'
    return 'On' if params.get('state') else 'Off'


def format_command_value(params):
    if not isinstance(params, dict) or 'value' not in params:
        return '--'
    return f'{params.get("value")}%'


def command_status_class(status):
    if status == CommandLog.Status.COMPLETED:
        return 'ok'
    if status == CommandLog.Status.FAILED:
        return 'bad'
    return 'warn'


def handle_rule_form(request):
    form_action = request.POST.get('form_action', 'save')
    rule_id = request.POST.get('rule_id')
    rule = AutomationRule.objects.filter(pk=rule_id).first() if rule_id else None

    if form_action == 'delete':
        if rule is not None:
            rule.delete()
            messages.success(request, 'Rule deleted.')
        return

    parsed = parse_rule_post(request.POST)
    if parsed['error']:
        messages.error(request, parsed['error'])
        return

    defaults = {
        'name': parsed['name'],
        'description': parsed['description'],
        'conditions': parsed['conditions'],
        'action': parsed['action'],
        'is_enabled': parsed['is_enabled'],
    }

    if rule is None:
        AutomationRule.objects.create(**defaults)
        messages.success(request, 'Rule created.')
    else:
        for field, value in defaults.items():
            setattr(rule, field, value)
        rule.save()
        messages.success(request, 'Rule updated.')


def parse_rule_post(post_data):
    name = post_data.get('name', '').strip()
    if not name:
        return {'error': 'Rule name is required.'}

    field = post_data.get('field')
    if field not in {'temperature', 'humidity', 'light'}:
        return {'error': 'Condition field is invalid.'}

    operator = post_data.get('operator')
    if operator not in {'>', '>=', '<', '<=', '==', '!='}:
        return {'error': 'Condition operator is invalid.'}

    try:
        threshold = float(post_data.get('threshold', ''))
    except ValueError:
        return {'error': 'Condition value must be a number.'}

    target = post_data.get('target')
    if target not in {'led', 'fan'}:
        return {'error': 'Target must be LED or fan.'}

    state = post_data.get('state')
    if state not in {'on', 'off'}:
        return {'error': 'Output state is invalid.'}

    try:
        value = round(float(post_data.get('value', '')))
    except ValueError:
        return {'error': 'Output value must be a number from 0 to 100.'}

    if value < 0 or value > 100:
        return {'error': 'Output value must be between 0 and 100.'}

    try:
        cooldown_seconds = int(post_data.get('cooldown_seconds', 60))
    except ValueError:
        return {'error': 'Cooldown must be a whole number.'}

    if cooldown_seconds < 0:
        return {'error': 'Cooldown must be 0 or greater.'}

    return {
        'error': '',
        'name': name,
        'description': post_data.get('description', '').strip(),
        'is_enabled': post_data.get('is_enabled') == 'on',
        'conditions': [
            {
                'field': field,
                'operator': operator,
                'value': threshold,
            }
        ],
        'action': {
            'name': 'set_output',
            'params': {
                'target': target,
                'state': state == 'on',
                'value': value,
            },
            'cooldown_seconds': cooldown_seconds,
        },
    }


def build_rule_rows(rules):
    return [
        {
            'rule': rule,
            'form': rule_form_data(rule),
            'condition_label': format_rule_condition(rule),
            'action_label': format_rule_action(rule),
        }
        for rule in rules
    ]


def rule_form_data(rule):
    condition = first_dict(rule.conditions)
    action = rule.action if isinstance(rule.action, dict) else {}
    params = action.get('params') if isinstance(action.get('params'), dict) else {}
    state = params.get('state')
    return {
        'field': condition.get('field', 'temperature'),
        'operator': condition.get('operator', '>'),
        'threshold': condition.get('value', 30),
        'target': params.get('target', 'fan'),
        'state': 'on' if state is not False else 'off',
        'value': params.get('value', 100 if state is not False else 0),
        'cooldown_seconds': action.get('cooldown_seconds', 60),
    }


def first_dict(value):
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def format_rule_condition(rule):
    condition = first_dict(rule.conditions)
    field = dict(rule_field_options()).get(condition.get('field'), condition.get('field', 'Sensor'))
    operator_label = dict(rule_operator_options()).get(condition.get('operator'), condition.get('operator', '?'))
    value = condition.get('value', '--')
    return f'{field} {operator_label} {value}'


def format_rule_action(rule):
    action = rule.action if isinstance(rule.action, dict) else {}
    params = action.get('params') if isinstance(action.get('params'), dict) else {}
    target = dict(rule_target_options()).get(params.get('target'), params.get('target', 'Output'))
    state = 'On' if params.get('state') else 'Off'
    value = params.get('value', '--')
    cooldown = action.get('cooldown_seconds', 60)
    return f'{target} {state}, {value}%, cooldown {cooldown}s'


def rule_field_options():
    return [
        ('temperature', 'Temperature'),
        ('humidity', 'Humidity'),
        ('light', 'Light'),
    ]


def rule_operator_options():
    return [
        ('>', 'is above'),
        ('>=', 'is at least'),
        ('<', 'is below'),
        ('<=', 'is at most'),
        ('==', 'equals'),
        ('!=', 'does not equal'),
    ]


def rule_target_options():
    return [
        ('fan', 'Fan'),
        ('led', 'LED'),
    ]


def rule_state_options():
    return [
        ('on', 'Turn on'),
        ('off', 'Turn off'),
    ]
