import asyncio
import json

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client, TransactionTestCase

from VDK.asgi import application
from control.models import CommandLog, OutputTarget
from gateway.consumers import command_from_intent, commands_from_intent
from gateway.protocol import get_esp32_state
from monitoring.models import SensorReading


class ESP32WebSocketTests(TransactionTestCase):
    def setUp(self):
        state = get_esp32_state()
        state.connected = False
        state.last_seen = None
        state.latest_sensor = None
        state.realtime_sensor_samples = []
        state.last_status = None
        state.audio_chunks_received = 0
        state.sensor_accumulator = None
        OutputTarget.objects.all().delete()
        CommandLog.objects.all().delete()

    async def test_sensor_payload_updates_ram_without_immediate_database_write(self):
        communicator = WebsocketCommunicator(application, '/ws/esp32/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            {
                'type': 'sensor_data',
                'data': {
                    'temperature': 30.5,
                    'humidity': 70.2,
                    'light': 410,
                },
            }
        )

        await wait_for(lambda: get_esp32_state().latest_sensor is not None)
        self.assertTrue(await communicator.receive_nothing(timeout=0.05))

        state = get_esp32_state()
        self.assertEqual(state.latest_sensor['humidity'], 70.2)
        self.assertEqual(len(state.realtime_sensor_samples), 1)
        self.assertEqual(await get_sensor_reading_count(), 0)

        await communicator.disconnect()

    async def test_sensor_payload_is_averaged_before_database_write(self):
        communicator = WebsocketCommunicator(application, '/ws/esp32/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            {
                'type': 'sensor_data',
                'data': {
                    'temperature': 30.0,
                    'humidity': 70.0,
                    'light': 400,
                },
            }
        )
        await wait_for(lambda: get_esp32_state().sensor_accumulator is not None)
        self.assertTrue(await communicator.receive_nothing(timeout=0.05))

        state = get_esp32_state()
        state.sensor_accumulator.window_started_at -= 181

        await communicator.send_json_to(
            {
                'type': 'sensor_data',
                'data': {
                    'temperature': 32.0,
                    'humidity': 72.0,
                    'light': 420,
                },
            }
        )
        self.assertTrue(await communicator.receive_nothing(timeout=0.05))

        reading = await get_latest_sensor_reading()
        self.assertEqual(reading['temperature'], 30.0)
        self.assertEqual(reading['humidity'], 70.0)
        self.assertEqual(reading['light'], 400.0)
        self.assertEqual(reading['sample_count'], 1)

        await communicator.disconnect()

    async def test_binary_audio_chunk_is_counted_without_ack(self):
        communicator = WebsocketCommunicator(application, '/ws/esp32/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_to(bytes_data=b'\x00\x01\x02\x03')
        await wait_for(lambda: get_esp32_state().audio_chunks_received >= 1)
        self.assertTrue(await communicator.receive_nothing(timeout=0.05))
        self.assertGreaterEqual(get_esp32_state().audio_chunks_received, 1)

        await communicator.disconnect()

    async def test_ping_still_returns_pong_for_heartbeat(self):
        communicator = WebsocketCommunicator(application, '/ws/esp32/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to({'type': 'ping'})
        response = json.loads(await communicator.receive_from())
        self.assertEqual(response['type'], 'pong')

        await communicator.disconnect()

    async def test_connect_sends_latest_led_and_fan_commands_from_database(self):
        await create_output_target('led', 'LED', 'light', {'target': 'led', 'state': True, 'value': 70})
        await create_output_target('fan', 'Fan', 'fan', {'target': 'fan', 'state': True, 'value': 40})

        communicator = WebsocketCommunicator(application, '/ws/esp32/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        messages = await receive_initial_sync_commands(communicator)
        self.assertEqual([message['params']['target'] for message in messages], ['led', 'fan'])
        self.assertEqual(messages[0]['params'], {'target': 'led', 'state': True, 'value': 70})
        self.assertEqual(messages[1]['params'], {'target': 'fan', 'state': True, 'value': 40})
        self.assertEqual(await get_system_command_count(), 2)

        await communicator.disconnect()


class VoiceIntentCommandTests(TransactionTestCase):
    def test_light_intent_value_maps_to_led_command_value(self):
        command_name, params = command_from_intent(
            {
                'action': 'turn_on',
                'device': 'light',
                'value': 50,
                'reply_message': 'Da bat den 50 phan tram.',
            }
        )

        self.assertEqual(command_name, 'set_output')
        self.assertEqual(params, {'target': 'led', 'state': True, 'value': 50})

    def test_missing_intent_value_keeps_default_command_value(self):
        command_name, params = command_from_intent(
            {
                'action': 'turn_on',
                'device': 'fan',
                'value': None,
                'reply_message': 'Da bat quat.',
            }
        )

        self.assertEqual(command_name, 'set_output')
        self.assertEqual(params, {'target': 'fan', 'state': True, 'value': 100})

    def test_multiple_intents_map_to_multiple_output_commands(self):
        commands = commands_from_intent(
            {
                'commands': [
                    {'action': 'turn_on', 'device': 'fan', 'value': None},
                    {'action': 'turn_on', 'device': 'light', 'value': None},
                ],
                'reply_message': 'Da bat quat va den.',
            }
        )

        self.assertEqual(
            commands,
            [
                ('set_output', {'target': 'fan', 'state': True, 'value': 100}),
                ('set_output', {'target': 'led', 'state': True, 'value': 100}),
            ],
        )


class ESP32CommandApiTests(TransactionTestCase):
    def setUp(self):
        state = get_esp32_state()
        state.connected = False
        state.last_seen = None
        state.latest_sensor = None
        state.realtime_sensor_samples = []
        state.last_status = None
        state.audio_chunks_received = 0
        state.sensor_accumulator = None
        OutputTarget.objects.all().delete()
        CommandLog.objects.all().delete()

    def test_send_command_creates_command_log_without_updating_output_before_ack(self):
        OutputTarget.objects.update_or_create(
            key='led',
            defaults={
                'name': 'LED',
                'kind': 'light',
                'current_state': {'target': 'led', 'state': False, 'value': 0},
                'is_enabled': True,
            },
        )

        response = Client().post(
            '/api/esp32/commands/',
            data=json.dumps(
                {
                    'name': 'set_output',
                    'params': {
                        'target': 'led',
                        'state': True,
                        'value': 80,
                    },
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()

        command = CommandLog.objects.get(command_id=body['command_id'])
        self.assertEqual(command.name, 'set_output')
        self.assertEqual(command.target, 'led')
        self.assertEqual(command.status, CommandLog.Status.SENT)

        output = OutputTarget.objects.get(key='led')
        self.assertEqual(output.current_state['state'], False)

    def test_send_command_validates_set_output_payload(self):
        response = Client().post(
            '/api/esp32/commands/',
            data=json.dumps(
                {
                    'name': 'set_output',
                    'params': {
                        'target': 'pump',
                        'state': True,
                        'value': 50,
                    },
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('target', response.json()['error'])

    def test_command_status_api_returns_command_and_output_state(self):
        OutputTarget.objects.update_or_create(
            key='led',
            defaults={
                'name': 'LED',
                'kind': 'light',
                'current_state': {'target': 'led', 'state': False, 'value': 0},
                'is_enabled': True,
            },
        )
        command = CommandLog.objects.create(
            command_id='abc123',
            name='set_output',
            target='led',
            params={'target': 'led', 'state': True, 'value': 80},
            status=CommandLog.Status.SENT,
        )

        response = Client().get(f'/api/esp32/commands/{command.command_id}/')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['command_id'], 'abc123')
        self.assertEqual(body['output_state']['value'], 0)

    async def test_command_ack_updates_command_log_and_output_target(self):
        await create_output_target('fan', 'Fan', 'fan')
        communicator = WebsocketCommunicator(application, '/ws/esp32/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await receive_initial_sync_commands(communicator, count=1)

        body = await post_command(
            {
                'name': 'set_output',
                'params': {
                    'target': 'fan',
                    'state': True,
                    'value': 65,
                },
            }
        )

        command_message = json.loads(await communicator.receive_from())
        self.assertEqual(command_message['type'], 'command')
        self.assertEqual(command_message['command_id'], body['command_id'])
        self.assertEqual(command_message['params']['value'], 65)

        await communicator.send_json_to(
            {
                'type': 'command_ack',
                'command_id': body['command_id'],
                'status': 'completed',
                'params': {
                    'target': 'fan',
                    'state': True,
                    'value': 65,
                },
            }
        )
        self.assertTrue(await communicator.receive_nothing(timeout=0.05))

        command = await get_command_log(body['command_id'])
        output = await get_output_state('fan')
        self.assertEqual(command['status'], CommandLog.Status.COMPLETED)
        self.assertIsNotNone(command['completed_at'])
        self.assertEqual(output['state'], True)
        self.assertEqual(output['value'], 65)

        await communicator.disconnect()


@database_sync_to_async
def get_latest_sensor_reading():
    reading = SensorReading.objects.latest('created_at')
    return {
        'temperature': reading.temperature,
        'humidity': reading.humidity,
        'light': reading.light,
        'sample_count': reading.raw_data.get('sample_count'),
    }


@database_sync_to_async
def get_sensor_reading_count():
    return SensorReading.objects.count()


@database_sync_to_async
def create_output_target(key, name, kind, current_state=None):
    OutputTarget.objects.update_or_create(
        key=key,
        defaults={
            'name': name,
            'kind': kind,
            'current_state': current_state or {'target': key, 'state': False, 'value': 0},
            'is_enabled': True,
        },
    )


@database_sync_to_async
def get_system_command_count():
    return CommandLog.objects.filter(source=CommandLog.Source.SYSTEM).count()


async def receive_initial_sync_commands(communicator, count=2):
    messages = []
    for _ in range(count):
        message = json.loads(await communicator.receive_from())
        assert message['type'] == 'command'
        assert message['name'] == 'set_output'
        messages.append(message)
    return messages


async def wait_for(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()


@database_sync_to_async
def post_command(payload):
    response = Client().post(
        '/api/esp32/commands/',
        data=json.dumps(payload),
        content_type='application/json',
    )
    assert response.status_code == 202
    return response.json()


@database_sync_to_async
def get_command_log(command_id):
    command = CommandLog.objects.get(command_id=command_id)
    return {
        'status': command.status,
        'completed_at': command.completed_at,
    }


@database_sync_to_async
def get_output_state(key):
    return OutputTarget.objects.get(key=key).current_state
