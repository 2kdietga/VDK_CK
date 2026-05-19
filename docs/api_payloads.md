# API And Payload Reference

Use this file as the demo checklist for Postman, ESP32 firmware, and the dashboard.

## Base URLs

Local:

```text
HTTP: http://127.0.0.1:8000
WS:   ws://127.0.0.1:8000
```

Render:

```text
HTTP: https://<your-render-service>.onrender.com
WS:   wss://<your-render-service>.onrender.com
```

## Human Pages

```text
GET /dashboard/
GET /dashboard/sensors/
GET /dashboard/controls/
GET /dashboard/commands/
GET /dashboard/rules/
GET /admin/
```

## Flow 1: ESP32 Connects

ESP32 opens:

```text
Local:  ws://127.0.0.1:8000/ws/esp32/
Render: wss://<your-render-service>.onrender.com/ws/esp32/
```

Check state:

```text
GET /api/esp32/
```

Example response:

```json
{
  "connected": true,
  "last_seen": 1780000000.123,
  "latest_sensor": null,
  "last_status": null,
  "audio_chunks_received": 0
}
```

## Flow 2: ESP32 Sends Sensor Data

WebSocket JSON text:

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

Server behavior:

```text
1. Updates latest_sensor in memory
2. Saves a monitoring.SensorReading row
3. Sends no ACK
```

View history:

```text
GET /dashboard/sensors/
```

## Flow 3: ESP32 Sends Status

WebSocket JSON text:

```json
{
  "type": "status",
  "wifi_rssi": -55,
  "free_heap": 182340
}
```

Server behavior:

```text
1. Updates last_status in memory
2. Sends no ACK
```

## Flow 4: ESP32 Sends Audio

ESP32 sends binary WebSocket frames.

Server behavior:

```text
1. Counts binary chunks
2. Does not process speech-to-text yet
3. Sends no ACK
```

Check count:

```text
GET /api/esp32/
```

## Flow 5: ESP32 Heartbeat

WebSocket JSON text:

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

## Flow 6: Parse Voice Text With Groq

Endpoint:

```text
POST /api/llm/intent/
```

Postman body:

```json
{
  "text": "Troi nong qua"
}
```

Expected response:

```json
{
  "action": "turn_on",
  "device": "fan",
  "reply_message": "Da bat quat."
}
```

Other examples:

```json
{
  "text": "Tat quat di"
}
```

Expected:

```json
{
  "action": "turn_off",
  "device": "fan",
  "reply_message": "Da tat quat."
}
```

```json
{
  "text": "Phong toi qua"
}
```

Expected:

```json
{
  "action": "turn_on",
  "device": "light",
  "reply_message": "Da bat den."
}
```

```json
{
  "text": "Nhiet do hien tai the nao"
}
```

Expected:

```json
{
  "action": "get_status",
  "device": "sensor",
  "reply_message": "Dang kiem tra du lieu cam bien."
}
```

Allowed output values:

```text
action: turn_on, turn_off, get_status
device: light, fan, sensor
reply_message: any non-empty short string
```

Possible errors:

```json
{
  "error": "`text` is required and must be a non-empty string."
}
```

```json
{
  "error": "GROQ_API_KEY is not configured."
}
```

```json
{
  "error": "LLM response did not contain a JSON object."
}
```

## Flow 7: Server Sends Command To ESP32

Endpoint:

```text
POST /api/esp32/commands/
```

Turn LED on:

```json
{
  "name": "set_output",
  "params": {
    "target": "led",
    "state": true
  }
}
```

Turn LED off:

```json
{
  "name": "set_output",
  "params": {
    "target": "led",
    "state": false
  }
}
```

Turn fan on:

```json
{
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true
  }
}
```

Turn fan off:

```json
{
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": false
  }
}
```

HTTP response:

```json
{
  "queued": true,
  "command_id": "generated-command-id",
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true
  }
}
```

ESP32 receives over WebSocket:

```json
{
  "type": "command",
  "command_id": "generated-command-id",
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true
  }
}
```

Server behavior:

```text
1. Sends command to the ESP32 WebSocket group
2. Creates a control.CommandLog row
3. Updates OutputTarget.current_state if target exists and is enabled
```

View logs:

```text
GET /dashboard/commands/
GET /dashboard/controls/
```

## Flow 8: Full Voice Control Demo

Step 1: Parse voice transcript:

```text
POST /api/llm/intent/
```

Body:

```json
{
  "text": "Troi nong qua"
}
```

Response:

```json
{
  "action": "turn_on",
  "device": "fan",
  "reply_message": "Da bat quat."
}
```

Step 2: Convert intent to command payload:

```text
device fan -> params.target fan
device light -> params.target led
turn_on -> state true
turn_off -> state false
get_status -> read latest sensor state, not implemented as ESP32 command yet
```

Command body:

```json
{
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true
  }
}
```

Step 3: Send command:

```text
POST /api/esp32/commands/
```

## Required Render Environment Variables

```text
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<strong-secret>
DATABASE_URL=<render-postgres-url>
LLM_PROVIDER=groq
GROQ_API_KEY=<groq-key>
GROQ_MODEL=llama-3.1-8b-instant
LLM_MIN_REQUEST_INTERVAL_SECONDS=2
```

No Redis is required for the demo setup.

## Render Commands

Build command:

```bash
bash build.sh
```

Start command:

```bash
python manage.py migrate && daphne -b 0.0.0.0 -p $PORT VDK.asgi:application
```

