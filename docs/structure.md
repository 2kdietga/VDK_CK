# Project Structure

```text
VDK/
  asgi.py       ASGI entrypoint; routes HTTP and WebSocket traffic
  urls.py       root URL config; exposes one /api/ entrypoint
  api_urls.py   shared API router for feature apps
  settings.py   Django settings and environment-based config

gateway/
  consumers.py  WebSocket consumer for the single ESP32 board
  protocol.py   ESP32 connection state and message constants
  routing.py    WebSocket route: /ws/esp32/
  views.py      HTTP state API: GET /api/esp32/
  urls.py       gateway API routes
  tests.py      gateway and command API tests

monitoring/
  models.py     SensorReading model
  admin.py      SensorReading admin

control/
  models.py     OutputTarget and CommandLog models
  views.py      command API: POST /api/esp32/commands/
  urls.py       control API routes
  admin.py      output and command admin

automation/
  models.py     AutomationRule model
  admin.py      rule admin

dashboard/
  views.py      server-rendered pages for overview, sensors, controls, commands, rules
  urls.py       dashboard routes under /dashboard/
  templates/    HTML templates for the UI

assistant_ai/
  services.py   OpenRouter REST API client
  views.py      intent parser API for voice transcript text
  urls.py       POST /api/llm/intent/
```

The root URL file has only one API mount:

```python
path('api/', include('VDK.api_urls'))
```

Then `VDK/api_urls.py` gathers the app-level API routes:

```python
path('', include('gateway.urls'))
path('', include('control.urls'))
path('', include('assistant_ai.urls'))
```

So the public API stays simple:

```text
GET  /api/esp32/
POST /api/esp32/commands/
WS   /ws/esp32/
```

The human-facing pages are:

```text
GET /dashboard/
GET /dashboard/sensors/
GET /dashboard/controls/
GET /dashboard/commands/
GET /dashboard/rules/
```
