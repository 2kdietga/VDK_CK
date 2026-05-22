from django.test import Client, TestCase

from automation.models import AutomationRule
from control.models import CommandLog


class DashboardPageTests(TestCase):
    def test_dashboard_pages_render(self):
        client = Client(HTTP_HOST='localhost')

        expected_statuses = {
            '/': 302,
            '/dashboard/': 200,
            '/dashboard/sensors/': 200,
            '/dashboard/controls/': 200,
            '/dashboard/commands/': 200,
            '/dashboard/rules/': 200,
        }

        for path, expected_status in expected_statuses.items():
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, expected_status)

    def test_rules_page_creates_updates_and_deletes_rule(self):
        client = Client(HTTP_HOST='localhost')

        create_response = client.post(
            '/dashboard/rules/',
            data={
                'form_action': 'save',
                'name': 'Hot room',
                'description': 'Turn fan on when hot',
                'is_enabled': 'on',
                'field': 'temperature',
                'operator': '>',
                'threshold': '30',
                'target': 'fan',
                'state': 'on',
                'value': '80',
                'cooldown_seconds': '45',
            },
        )

        self.assertEqual(create_response.status_code, 302)
        rule = AutomationRule.objects.get(name='Hot room')
        self.assertEqual(rule.conditions[0]['field'], 'temperature')
        self.assertEqual(rule.action['params']['value'], 80)

        update_response = client.post(
            '/dashboard/rules/',
            data={
                'form_action': 'save',
                'rule_id': rule.id,
                'name': 'Dark room',
                'description': '',
                'field': 'light',
                'operator': '<',
                'threshold': '300',
                'target': 'led',
                'state': 'on',
                'value': '100',
                'cooldown_seconds': '60',
            },
        )

        self.assertEqual(update_response.status_code, 302)
        rule.refresh_from_db()
        self.assertEqual(rule.name, 'Dark room')
        self.assertFalse(rule.is_enabled)
        self.assertEqual(rule.conditions[0]['field'], 'light')
        self.assertEqual(rule.action['params']['target'], 'led')

        delete_response = client.post(
            '/dashboard/rules/',
            data={
                'form_action': 'delete',
                'rule_id': rule.id,
            },
        )

        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(AutomationRule.objects.filter(id=rule.id).exists())

    def test_commands_page_renders_readable_command_fields(self):
        CommandLog.objects.create(
            command_id='abc123456789',
            name='set_output',
            target='fan',
            params={'target': 'fan', 'state': True, 'value': 80},
            source=CommandLog.Source.RULE,
            status=CommandLog.Status.SENT,
        )

        response = Client(HTTP_HOST='localhost').get('/dashboard/commands/')

        self.assertContains(response, 'abc12345')
        self.assertContains(response, 'Rule')
        self.assertContains(response, 'On')
        self.assertContains(response, '80%')
