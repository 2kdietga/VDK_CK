import json
from unittest.mock import patch

from django.test import Client, TestCase

from .services import extract_openrouter_text, parse_json_object, wait_between_llm_requests


class LLMIntentApiTests(TestCase):
    def test_intent_api_requires_text(self):
        response = Client().post(
            '/api/llm/intent/',
            data=json.dumps({}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_intent_api_reports_missing_openrouter_api_key(self):
        with patch.dict('os.environ', {'LLM_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': ''}):
            response = Client().post(
                '/api/llm/intent/',
                data=json.dumps({'text': 'Trời nóng quá'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn('OPENROUTER_API_KEY', response.json()['error'])

    @patch('assistant_ai.views.parse_iot_intent')
    def test_intent_api_returns_action_json(self, parse_iot_intent):
        parse_iot_intent.return_value = {
            'action_device': 'fan',
            'action_command': 'ON',
        }

        response = Client().post(
            '/api/llm/intent/',
            data=json.dumps({'text': 'Trời nóng quá'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                'action_device': 'fan',
                'action_command': 'ON',
            },
        )

    def test_parse_json_object_handles_markdown_fence(self):
        parsed = parse_json_object(
            '```json\n{"action_device": "fan", "action_command": "ON"}\n```'
        )

        self.assertEqual(parsed['action_device'], 'fan')

    def test_extract_openrouter_text(self):
        text = extract_openrouter_text(
            {
                'choices': [
                    {
                        'message': {
                            'content': '{"action_device":"fan","action_command":"ON"}',
                        }
                    }
                ]
            }
        )

        self.assertIn('action_device', text)

    @patch('assistant_ai.services.time.sleep')
    @patch('assistant_ai.services.time.monotonic')
    def test_rate_limit_waits_only_when_calls_are_too_close(self, monotonic, sleep):
        import assistant_ai.services as services

        services._last_llm_request_at = 0.0
        monotonic.side_effect = [10.0, 10.0, 11.0, 13.0]

        wait_between_llm_requests(2.0)
        wait_between_llm_requests(2.0)

        sleep.assert_called_once_with(1.0)
