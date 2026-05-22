import json
from unittest.mock import patch

from django.test import Client, TestCase

from automation.models import AutomationRule
from control.models import OutputTarget
from gateway.protocol import get_esp32_state

from .services import (
    LLMIntentParseError,
    LLMResponse,
    build_llm_user_content,
    extract_groq_text,
    get_current_iot_context,
    parse_intent_commands,
    parse_iot_intent,
    parse_json_object,
    parse_output_value,
    wait_between_llm_requests,
)


class LLMIntentApiTests(TestCase):
    def test_intent_api_requires_text(self):
        response = Client().post(
            '/api/llm/intent/',
            data=json.dumps({}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_intent_api_reports_missing_groq_api_key(self):
        with patch.dict('os.environ', {'LLM_PROVIDER': 'groq', 'GROQ_API_KEY': ''}):
            response = Client().post(
                '/api/llm/intent/',
                data=json.dumps({'text': 'Trời nóng quá'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn('GROQ_API_KEY', response.json()['error'])

    @patch('assistant_ai.views.parse_iot_intent')
    def test_intent_api_returns_action_json(self, parse_iot_intent):
        parse_iot_intent.return_value = {
            'action': 'turn_on',
            'device': 'fan',
            'reply_message': 'Đã bật quạt.',
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
                'action': 'turn_on',
                'device': 'fan',
                'reply_message': 'Đã bật quạt.',
            },
        )

    @patch('assistant_ai.views.parse_iot_intent')
    def test_intent_api_applies_automation_rule_requests(self, parse_iot_intent):
        parse_iot_intent.return_value = {
            'commands': [],
            'automation_rules': [
                {
                    'operation': 'create',
                    'name': 'Hot fan',
                    'condition': {'field': 'temperature', 'operator': '>', 'value': 30},
                    'action': {'target': 'fan', 'state': True, 'value': 80, 'cooldown_seconds': 60},
                    'is_enabled': True,
                }
            ],
            'reply_message': 'Da tao rule.',
        }

        response = Client().post(
            '/api/llm/intent/',
            data=json.dumps({'text': 'khi nhiet do tren 30 thi bat quat'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['automation_applied'])
        self.assertTrue(AutomationRule.objects.filter(name='Hot fan').exists())

    def test_parse_json_object_handles_markdown_fence(self):
        parsed = parse_json_object(
            '```json\n{"action": "turn_on", "device": "fan", "reply_message": "Đã bật quạt."}\n```'
        )

        self.assertEqual(parsed['device'], 'fan')

    def test_parse_output_value_accepts_percentage_string(self):
        self.assertEqual(parse_output_value('50%'), 50)
        self.assertEqual(parse_output_value(65.4), 65)
        self.assertIsNone(parse_output_value(None))

    def test_parse_output_value_rejects_out_of_range_value(self):
        with self.assertRaises(LLMIntentParseError):
            parse_output_value(120)

    @patch('assistant_ai.services.chat_with_llm')
    def test_parse_iot_intent_returns_value(self, chat_with_llm):
        chat_with_llm.return_value = LLMResponse(
            text='{"action":"turn_on","device":"light","value":"50%","reply_message":"Da bat den 50 phan tram."}',
            raw={},
        )

        intent = parse_iot_intent('bat den do sang 50 phan tram')

        self.assertEqual(intent['action'], 'turn_on')
        self.assertEqual(intent['device'], 'light')
        self.assertEqual(intent['value'], 50)
        self.assertEqual(intent['commands'], [{'action': 'turn_on', 'device': 'light', 'value': 50}])

    @patch('assistant_ai.services.chat_with_llm')
    def test_parse_iot_intent_accepts_multiple_commands(self, chat_with_llm):
        chat_with_llm.return_value = LLMResponse(
            text=(
                '{"commands":['
                '{"action":"turn_on","device":"fan","value":null},'
                '{"action":"turn_on","device":"light","value":null}'
                '],"reply_message":"Da bat quat va den."}'
            ),
            raw={},
        )

        intent = parse_iot_intent('toi nong va troi toi qua')

        self.assertEqual(
            intent,
            {
                'automation_rules': [],
                'commands': [
                    {'action': 'turn_on', 'device': 'fan', 'value': None},
                    {'action': 'turn_on', 'device': 'light', 'value': None},
                ],
                'reply_message': 'Da bat quat va den.',
            },
        )

    @patch('assistant_ai.services.chat_with_llm')
    def test_parse_iot_intent_accepts_automation_rule_request(self, chat_with_llm):
        chat_with_llm.return_value = LLMResponse(
            text=(
                '{"commands":[],"automation_rules":['
                '{"operation":"create","name":"Hot fan",'
                '"condition":{"field":"temperature","operator":">","value":30},'
                '"action":{"target":"fan","state":true,"value":80,"cooldown_seconds":45}}'
                '],"reply_message":"Da tao rule bat quat khi nong."}'
            ),
            raw={},
        )

        intent = parse_iot_intent('khi nhiet do tren 30 thi bat quat 80 phan tram')

        self.assertEqual(intent['commands'], [])
        self.assertEqual(intent['automation_rules'][0]['operation'], 'create')
        self.assertEqual(intent['automation_rules'][0]['condition']['field'], 'temperature')
        self.assertEqual(intent['automation_rules'][0]['action']['target'], 'fan')
        self.assertEqual(intent['automation_rules'][0]['action']['value'], 80)

    @patch('assistant_ai.services.chat_with_llm')
    def test_parse_iot_intent_treats_top_level_create_as_automation_rule(self, chat_with_llm):
        chat_with_llm.return_value = LLMResponse(
            text=(
                '{"action":"create","name":"Hot fan",'
                '"condition":{"field":"temperature","operator":">","value":30},'
                '"rule_action":{"target":"fan","state":true,"value":80},'
                '"reply_message":"Da tao rule bat quat khi nong."}'
            ),
            raw={},
        )

        intent = parse_iot_intent('khi nhiet do tren 30 thi bat quat 80 phan tram')

        self.assertEqual(intent['commands'], [])
        self.assertEqual(intent['automation_rules'][0]['operation'], 'create')
        self.assertEqual(intent['automation_rules'][0]['condition']['value'], 30)
        self.assertEqual(intent['automation_rules'][0]['action']['value'], 80)

    @patch('assistant_ai.services.chat_with_llm')
    def test_parse_iot_intent_drops_immediate_command_from_automation_request(self, chat_with_llm):
        chat_with_llm.return_value = LLMResponse(
            text=(
                '{"commands":[{"action":"turn_on","device":"fan","value":50}],'
                '"automation_rules":[{"operation":"create","name":"Cool fan",'
                '"condition":{"field":"temperature","operator":"<","value":50},'
                '"action":{"target":"fan","state":true,"value":50,"cooldown_seconds":60}}],'
                '"reply_message":"Da tao rule."}'
            ),
            raw={},
        )

        intent = parse_iot_intent('khi nhiet do be hon 50 thi bat quat 50 phan tram')

        self.assertEqual(intent['commands'], [])
        self.assertNotIn('action', intent)

    @patch('assistant_ai.services.chat_with_llm')
    def test_parse_iot_intent_overrides_generic_sensor_reply_with_context_value(self, chat_with_llm):
        chat_with_llm.return_value = LLMResponse(
            text=(
                '{"commands":[{"action":"get_status","device":"sensor","value":null}],'
                '"reply_message":"Dang kiem tra do am."}'
            ),
            raw={},
        )

        intent = parse_iot_intent(
            'độ ẩm hiện tại bao nhiêu',
            context={
                'latest_sensor': {
                    'temperature': 31.0,
                    'humidity': 65.0,
                    'light': 420.0,
                },
                'outputs': {},
            },
        )

        self.assertEqual(intent['reply_message'], 'Độ ẩm hiện tại là 65%.')

    @patch('assistant_ai.services.chat_with_llm')
    def test_parse_iot_intent_overrides_generic_output_reply_with_context_value(self, chat_with_llm):
        chat_with_llm.return_value = LLMResponse(
            text=(
                '{"commands":[{"action":"get_status","device":"sensor","value":null}],'
                '"reply_message":"Dang kiem tra den."}'
            ),
            raw={},
        )

        intent = parse_iot_intent(
            'đèn đang bật không',
            context={
                'latest_sensor': {},
                'outputs': {
                    'led': {
                        'state': True,
                        'value': 80,
                    }
                },
            },
        )

        self.assertEqual(intent['reply_message'], 'Đèn hiện đang bật, mức 80 phần trăm.')

    def test_parse_intent_commands_rejects_empty_commands(self):
        with self.assertRaises(LLMIntentParseError):
            parse_intent_commands({'commands': [], 'reply_message': 'Khong co lenh.'})

    def test_build_llm_user_content_includes_current_context(self):
        content = build_llm_user_content(
            'nhiet do bao nhieu',
            context={'latest_sensor': {'temperature': 30.5}, 'outputs': {'fan': {'value': 70}}},
        )

        self.assertIn('CURRENT_SYSTEM_CONTEXT', content)
        self.assertIn('"temperature": 30.5', content)
        self.assertIn('USER_TRANSCRIPT', content)

    def test_get_current_iot_context_includes_sensor_and_outputs(self):
        state = get_esp32_state()
        state.connected = True
        state.latest_sensor = {
            'timestamp': 123,
            'temperature': 31.0,
            'humidity': 65.0,
            'light': 420.0,
        }
        OutputTarget.objects.update_or_create(
            key='fan',
            defaults={
                'name': 'Fan',
                'kind': 'fan',
                'current_state': {'target': 'fan', 'state': True, 'value': 80},
                'is_enabled': True,
            },
        )

        context = get_current_iot_context()

        self.assertEqual(context['latest_sensor']['temperature'], 31.0)
        self.assertEqual(context['outputs']['fan']['value'], 80)

    def test_extract_groq_text(self):
        text = extract_groq_text(
            {
                'choices': [
                    {
                        'message': {
                            'content': '{"action":"turn_on","device":"fan","reply_message":"Đã bật quạt."}',
                        }
                    }
                ]
            }
        )

        self.assertIn('turn_on', text)

    @patch('assistant_ai.services.time.sleep')
    @patch('assistant_ai.services.time.monotonic')
    def test_rate_limit_waits_only_when_calls_are_too_close(self, monotonic, sleep):
        import assistant_ai.services as services

        services._last_llm_request_at = 0.0
        monotonic.side_effect = [10.0, 10.0, 11.0, 13.0]

        wait_between_llm_requests(2.0)
        wait_between_llm_requests(2.0)

        sleep.assert_called_once_with(1.0)
