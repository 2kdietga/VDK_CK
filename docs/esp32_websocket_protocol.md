# ESP32 WebSocket Protocol

Server endpoint for the single ESP32 board:

```text
ws://<server-ip>:8000/ws/esp32/
```

Local example:

```text
ws://127.0.0.1:8000/ws/esp32/
```

LAN example for ESP32:

```text
ws://192.168.1.10:8000/ws/esp32/
```

## ESP32 -> Server

Sensor data is sent as JSON text. The server stores the latest payload and does not send an ACK.

```json
{
  "type": "sensor_data",
  "data": {
    "temperature": 30.5,
    "humidity": 70.2,
    "light": 410
  }
}
```

ESP32 status is also JSON text. The server stores it and does not send an ACK.

```json
{
  "type": "status",
  "wifi_rssi": -55,
  "free_heap": 182340
}
```

Audio input is sent as raw PCM binary WebSocket frames:

```text
PCM 16-bit signed little-endian, mono, 16 kHz
```

After the last binary frame for one voice request, ESP32 sends:

```json
{
  "type": "audio_end"
}
```

The server wraps the buffered PCM as an in-memory WAV, transcribes it with
`speech_recognition`, asks the LLM for an IoT intent, sends the derived command
to ESP32, converts the LLM `reply_message` to PCM 16-bit mono 16 kHz with
VoiceRSS, amplifies the PCM, and streams it back as binary WebSocket frames.

Heartbeat is the only request-response message:

```json
{
  "type": "ping"
}
```

Server response:

```json
{
  "type": "pong"
}
```

After ESP32 executes a server command, it must send a command ACK. The server uses this message to mark the command as completed or failed and to save the output state in the database.

```json
{
  "type": "command_ack",
  "command_id": "generated-id",
  "status": "completed",
  "params": {
    "target": "led",
    "state": true,
    "value": 80
  }
}
```

Use `status: "failed"` if ESP32 could not execute the command.

## Server -> ESP32

Voice command text can be parsed by the LLM intent endpoint before sending a command to ESP32:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/llm/intent/ `
  -ContentType application/json `
  -Body '{"text":"Troi nong qua"}'
```

Expected response:

```json
{
  "action": "turn_on",
  "device": "fan",
  "value": null,
  "reply_message": "Da bat quat."
}
```

If the user says a level such as `Bat den do sang 50 phan tram`, the LLM intent can include `value`:

```json
{
  "action": "turn_on",
  "device": "light",
  "value": 50,
  "reply_message": "Da bat den 50 phan tram."
}
```

Send a command through HTTP while the ESP32 WebSocket is connected:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/esp32/commands/ `
  -ContentType application/json `
  -Body '{"name":"set_output","params":{"target":"led","state":true}}'
```

ESP32 receives:

```json
{
  "type": "command",
  "command_id": "generated-id",
  "name": "set_output",
  "params": {
    "target": "led",
    "state": true,
    "value": 80
  }
}
```

During a voice response, ESP32 receives this sequence:

```json
{
  "type": "stop_listen"
}
```

Then one or more binary PCM frames:

```text
PCM 16-bit signed little-endian, mono, 16 kHz
```

Finally:

```json
{
  "type": "audio_done"
}
```

For a fan command, keep the same transport and change the command params:

```json
{
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true
  }
}
```

Check current in-memory ESP32 state:

```text
GET http://127.0.0.1:8000/api/esp32/
```

## Local And Deploy Settings

The project uses Django Channels with the in-memory channel layer. This is enough for the intended demo setup:

```text
1 Django server
1 ESP32 board
1 dashboard for demo
no multi-server scaling
```

When testing from a physical ESP32 through LAN, add your computer IP to `DJANGO_ALLOWED_HOSTS`.

For deploy, set:

```text
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<strong-secret>
DJANGO_ALLOWED_HOSTS=<your-domain>,<your-server-ip>
LLM_PROVIDER=groq
GROQ_API_KEY=<your-groq-api-key>
GROQ_MODEL=llama-3.1-8b-instant
LLM_MIN_REQUEST_INTERVAL_SECONDS=2
VOICERSS_API_KEY=<your-voicerss-api-key>
VOICERSS_LANGUAGE=vi-vn
VOICERSS_VOICE=Chi
SPEECH_RECOGNITION_LANGUAGE=vi-VN
TTS_VOLUME_GAIN=1.8
```

## Minimal ESP32 Message Flow

1. Connect to `ws://<server-ip>:8000/ws/esp32/`.
2. Send `sensor_data` every few seconds.
3. Send binary audio chunks when recording voice.
4. Send `{"type":"audio_end"}` when one recording is complete.
5. Send `ping` only when firmware wants to check that the connection is alive.
6. Listen for `command`, `stop_listen`, binary audio, and `audio_done` messages from the server.
7. Execute command params such as `{"target":"led","state":true,"value":80}`.
8. Send `command_ack` with the same `command_id` after execution.
