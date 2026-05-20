import json

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client, TransactionTestCase

from VDK.asgi import application
from control.models import CommandLog, OutputTarget
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


class ESP32CommandApiTests(TransactionTestCase):
    def test_send_command_creates_command_log_and_updates_output_target(self):
        OutputTarget.objects.create(
            key='led',
            name='LED',
            kind='light',
            current_state={'state': False},
        )

        response = Client().post(
            '/api/esp32/commands/',
            data=json.dumps(
                {
                    'name': 'set_output',
                    'params': {
                        'target': 'led',
                        'state': True,
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
        self.assertEqual(output.current_state['state'], True)


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
