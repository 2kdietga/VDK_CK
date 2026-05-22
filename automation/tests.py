from django.test import TestCase

from control.models import CommandLog, OutputTarget

from .models import AutomationRule
from .services import apply_automation_rule_requests, evaluate_automation_rules


class AutomationRuleEngineTests(TestCase):
    def test_voice_rule_request_creates_or_updates_rule(self):
        results = apply_automation_rule_requests(
            [
                {
                    'operation': 'create',
                    'name': 'Hot fan',
                    'condition': {'field': 'temperature', 'operator': '>', 'value': 30},
                    'action': {'target': 'fan', 'state': True, 'value': 80, 'cooldown_seconds': 45},
                    'is_enabled': True,
                }
            ]
        )

        rule = AutomationRule.objects.get(name='Hot fan')
        self.assertEqual(results, ['Created automation rule: Hot fan.'])
        self.assertEqual(rule.conditions[0]['field'], 'temperature')
        self.assertEqual(rule.action['params']['target'], 'fan')
        self.assertEqual(rule.action['params']['value'], 80)

        apply_automation_rule_requests(
            [
                {
                    'operation': 'update',
                    'name': 'Hot fan',
                    'condition': {'field': 'temperature', 'operator': '>', 'value': 32},
                    'action': {'target': 'fan', 'state': True, 'value': 90, 'cooldown_seconds': 30},
                    'is_enabled': True,
                }
            ]
        )

        rule.refresh_from_db()
        self.assertEqual(rule.conditions[0]['value'], 32)
        self.assertEqual(rule.action['params']['value'], 90)

    def test_voice_rule_request_can_disable_and_delete_rule(self):
        AutomationRule.objects.create(
            name='Hot fan',
            conditions=[{'field': 'temperature', 'operator': '>', 'value': 30}],
            action={'name': 'set_output', 'params': {'target': 'fan', 'state': True, 'value': 80}},
            is_enabled=True,
        )

        apply_automation_rule_requests([{'operation': 'disable', 'name': 'Hot fan'}])
        rule = AutomationRule.objects.get(name='Hot fan')
        self.assertFalse(rule.is_enabled)

        apply_automation_rule_requests([{'operation': 'delete', 'name': 'Hot fan'}])
        self.assertFalse(AutomationRule.objects.filter(name='Hot fan').exists())

    def test_voice_rule_request_disables_conflicting_rule(self):
        AutomationRule.objects.create(
            name='Fan on when hot',
            conditions=[{'field': 'temperature', 'operator': '>', 'value': 30}],
            action={
                'name': 'set_output',
                'params': {'target': 'fan', 'state': True, 'value': 80},
                'cooldown_seconds': 60,
            },
            is_enabled=True,
        )

        results = apply_automation_rule_requests(
            [
                {
                    'operation': 'create',
                    'name': 'Fan off when very hot',
                    'condition': {'field': 'temperature', 'operator': '>', 'value': 35},
                    'action': {'target': 'fan', 'state': False, 'value': 0, 'cooldown_seconds': 60},
                    'is_enabled': True,
                }
            ]
        )

        old_rule = AutomationRule.objects.get(name='Fan on when hot')
        new_rule = AutomationRule.objects.get(name='Fan off when very hot')
        self.assertFalse(old_rule.is_enabled)
        self.assertTrue(new_rule.is_enabled)
        self.assertIn('Disabled conflicting rules: Fan on when hot.', results[0])

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
