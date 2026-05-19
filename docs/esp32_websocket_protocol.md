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

Audio input is sent as binary WebSocket frames. The server currently counts received chunks and does not send an ACK.

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
  "reply_message": "Da bat quat."
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
    "state": true
  }
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
```

## Minimal ESP32 Message Flow

1. Connect to `ws://<server-ip>:8000/ws/esp32/`.
2. Send `sensor_data` every few seconds.
3. Send binary audio chunks when recording voice.
4. Send `ping` only when firmware wants to check that the connection is alive.
5. Listen for `command` messages from the server.
6. Execute command params such as `{"target":"led","state":true}`.
