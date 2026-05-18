from django.test import Client, TestCase


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
