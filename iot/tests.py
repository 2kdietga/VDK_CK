import json

from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase

from VDK.asgi import application
from iot.protocol import get_esp32_state


class ESP32WebSocketTests(TransactionTestCase):
    async def test_sensor_payload_is_stored_without_ack(self):
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
