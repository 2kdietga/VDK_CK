from django.test import TestCase

from control.models import CommandLog, OutputTarget

from .models import AutomationRule
from .services import evaluate_automation_rules


class AutomationRuleEngineTests(TestCase):
    def test_matching_rule_builds_command_and_command_log(self):
        OutputTarget.objects.update_or_create(
            key='fan',
            defaults={
                'name': 'Fan',
                'kind': 'fan',
                'current_state': {'target': 'fan', 'state': False, 'value': 0},
                'is_enabled': True,
            },
        )
        AutomationRule.objects.create(
            name='Hot room turns fan on',
            conditions=[{'field': 'temperature', 'operator': '>', 'value': 30}],
            action={
                'name': 'set_output',
                'params': {'target': 'fan', 'state': True, 'value': 80},
            },
        )

        commands = evaluate_automation_rules({'temperature': 31})

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]['name'], 'set_output')
        self.assertEqual(commands[0]['params'], {'target': 'fan', 'state': True, 'value': 80})

        command_log = CommandLog.objects.get(command_id=commands[0]['command_id'])
        self.assertEqual(command_log.source, CommandLog.Source.RULE)
        self.assertEqual(command_log.target, 'fan')

    def test_rule_does_not_fire_when_output_already_matches(self):
        OutputTarget.objects.update_or_create(
            key='led',
            defaults={
                'name': 'LED',
                'kind': 'light',
                'current_state': {'target': 'led', 'state': True, 'value': 100},
                'is_enabled': True,
            },
        )
        AutomationRule.objects.create(
            name='Dark room turns LED on',
            conditions=[{'field': 'light', 'operator': '<', 'value': 300}],
            action={
                'name': 'set_output',
                'params': {'target': 'led', 'state': True, 'value': 100},
            },
        )

        commands = evaluate_automation_rules({'light': 250})

        self.assertEqual(commands, [])
        self.assertEqual(CommandLog.objects.count(), 0)

    def test_rule_cooldown_prevents_duplicate_commands(self):
        OutputTarget.objects.update_or_create(
            key='fan',
            defaults={
                'name': 'Fan',
                'kind': 'fan',
                'current_state': {'target': 'fan', 'state': False, 'value': 0},
                'is_enabled': True,
            },
        )
        AutomationRule.objects.create(
            name='Hot room turns fan on',
            conditions=[{'field': 'temperature', 'operator': '>', 'value': 30}],
            action={
                'name': 'set_output',
                'params': {'target': 'fan', 'state': True, 'value': 80},
                'cooldown_seconds': 60,
            },
        )

        first_commands = evaluate_automation_rules({'temperature': 31})
        second_commands = evaluate_automation_rules({'temperature': 32})

        self.assertEqual(len(first_commands), 1)
        self.assertEqual(second_commands, [])
        self.assertEqual(CommandLog.objects.count(), 1)
