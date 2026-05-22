from django.test import TestCase

from .alerts import build_temperature_alert_decision, calculate_temperature_risk
from .models import SensorReading


class TemperatureAlertTests(TestCase):
    def test_high_temperature_uses_absolute_threshold(self):
        risk_score, reasons = calculate_temperature_risk(40.5, [])

        self.assertGreaterEqual(risk_score, 70)
        self.assertIn('nhiet do vuot nguong nguy hiem', reasons)

    def test_temperature_risk_uses_average_and_trend_history(self):
        SensorReading.objects.create(temperature=30, humidity=70, light=400)
        SensorReading.objects.create(temperature=29, humidity=70, light=400)
        history = list(SensorReading.objects.order_by('-created_at')[:10])

        risk_score, reasons = calculate_temperature_risk(35, history)

        self.assertGreaterEqual(risk_score, 50)
        self.assertIn('cao hon trung binh gan day', reasons)
        self.assertIn('nhiet do tang nhanh', reasons)

    def test_temperature_alert_respects_cooldown_without_escalation(self):
        decision = build_temperature_alert_decision(
            current_temp=36,
            history=[],
            last_level='warning',
            last_alert_at=100,
            now=120,
        )

        self.assertEqual(decision.level, 'warning')
        self.assertFalse(decision.should_alert)

    def test_temperature_alert_escalates_immediately(self):
        history = [SensorReading(temperature=30)]
        decision = build_temperature_alert_decision(
            current_temp=41,
            history=history,
            last_level='warning',
            last_alert_at=100,
            now=120,
        )

        self.assertEqual(decision.level, 'critical')
        self.assertTrue(decision.should_alert)
