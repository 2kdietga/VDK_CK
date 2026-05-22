from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from gateway.protocol import coerce_float, get_esp32_state

from .models import SensorReading


ALERT_COOLDOWN_SECONDS = 60
WARNING_RISK_SCORE = 40
CRITICAL_RISK_SCORE = 80
TEMPERATURE_RECOVERY_THRESHOLD = 33.0
LEVEL_RANK = {
    None: 0,
    'warning': 1,
    'critical': 2,
}


@dataclass(slots=True)
class AlertDecision:
    should_alert: bool = False
    key: str | None = None
    level: str | None = None
    message: str = ''
    risk_score: int = 0
    reasons: list[str] = field(default_factory=list)


def evaluate_temperature_alert(sensor_data: dict[str, Any]) -> AlertDecision:
    current_temp = coerce_float(sensor_data.get('temperature'))
    if current_temp is None:
        return AlertDecision()

    history = list(SensorReading.objects.order_by('-created_at')[:10])
    state = get_esp32_state()
    now = time.time()
    decision = build_temperature_alert_decision(
        current_temp=current_temp,
        history=history,
        last_level=state.alert_level,
        last_alert_at=state.last_alert_at,
        now=now,
    )

    if decision.level is None and current_temp <= TEMPERATURE_RECOVERY_THRESHOLD:
        state.alert_active_key = None
        state.alert_level = None
        return decision

    if decision.level is not None:
        state.alert_active_key = decision.key
        state.alert_level = decision.level

    if decision.should_alert:
        state.last_alert_at = now

    return decision


def build_temperature_alert_decision(
    current_temp: float,
    history: list[SensorReading],
    last_level: str | None = None,
    last_alert_at: float | None = None,
    now: float | None = None,
) -> AlertDecision:
    risk_score, reasons = calculate_temperature_risk(current_temp, history)
    level = risk_level(risk_score)
    if level is None:
        return AlertDecision(risk_score=risk_score, reasons=reasons)

    now = now if now is not None else time.time()
    escalated = LEVEL_RANK[level] > LEVEL_RANK.get(last_level, 0)
    cooldown_elapsed = last_alert_at is None or now - last_alert_at >= ALERT_COOLDOWN_SECONDS
    should_alert = escalated or cooldown_elapsed

    return AlertDecision(
        should_alert=should_alert,
        key='temperature',
        level=level,
        message=temperature_alert_message(level, current_temp, risk_score, reasons),
        risk_score=risk_score,
        reasons=reasons,
    )


def calculate_temperature_risk(current_temp: float, history: list[SensorReading]) -> tuple[int, list[str]]:
    risk_score = 0
    reasons = []

    if current_temp >= 40:
        risk_score += 70
        reasons.append('nhiet do vuot nguong nguy hiem')
    elif current_temp >= 35:
        risk_score += 40
        reasons.append('nhiet do cao')

    temperatures = [
        reading.temperature
        for reading in history
        if reading.temperature is not None
    ]
    if temperatures:
        average_temp = sum(temperatures) / len(temperatures)
        average_delta = current_temp - average_temp
        if average_delta >= 5:
            risk_score += 30
            reasons.append('cao hon trung binh gan day')
        elif average_delta >= 3:
            risk_score += 15
            reasons.append('nhinh hon trung binh gan day')

        latest_delta = current_temp - temperatures[0]
        if latest_delta >= 4:
            risk_score += 25
            reasons.append('nhiet do tang nhanh')
        elif latest_delta >= 2:
            risk_score += 10
            reasons.append('nhiet do dang tang')

    return risk_score, reasons


def risk_level(risk_score: int) -> str | None:
    if risk_score >= CRITICAL_RISK_SCORE:
        return 'critical'
    if risk_score >= WARNING_RISK_SCORE:
        return 'warning'
    return None


def temperature_alert_message(
    level: str,
    current_temp: float,
    risk_score: int,
    reasons: list[str],
) -> str:
    formatted_temp = format_temperature(current_temp)
    reason_text = ', '.join(reasons) if reasons else 'nhiet do bat thuong'

    if level == 'critical':
        return (
            f'Cảnh báo nguy hiểm, nhiệt độ hiện tại là {formatted_temp} độ C, '
            f'{reason_text}. Điểm rủi ro: {risk_score}.'
        )

    return (
        f'ảnh báo, nhiệt độ hiện tại là {formatted_temp} độ C, '
        f'{reason_text}. Điểm rủi ro: {risk_score}.'
    )


def format_temperature(value: float) -> str:
    numeric_value = float(value)
    if numeric_value.is_integer():
        return str(round(numeric_value))
    return f'{numeric_value:.1f}'.rstrip('0').rstrip('.')
