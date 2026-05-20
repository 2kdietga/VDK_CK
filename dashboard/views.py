from __future__ import annotations

from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

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
        'recent_commands': CommandLog.objects.all()[:8],
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
    context = {
        'outputs': OutputTarget.objects.all(),
    }
    return render(request, 'dashboard/controls.html', context)


@require_GET
def commands(request):
    context = {
        'commands': CommandLog.objects.all()[:100],
    }
    return render(request, 'dashboard/commands.html', context)


@require_GET
def rules(request):
    context = {
        'rules': AutomationRule.objects.all(),
    }
    return render(request, 'dashboard/rules.html', context)


def home(request):
    return redirect('dashboard:overview')
