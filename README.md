# AIoT Monitor - ESP32, Django Channels va tro ly giong noi

Du an xay dung backend Django cho he thong AIoT giam sat moi truong va dieu khien thiet bi qua ESP32. Server nhan du lieu cam bien theo thoi gian thuc bang WebSocket, luu lich su vao database, hien thi dashboard, gui lenh dieu khien nguoc ve ESP32 va co API phan tich cau lenh giong noi dang text bang Groq LLM.

## Tinh nang hien co

- Ket noi WebSocket cho 1 ESP32 tai `/ws/esp32/`.
- Nhan JSON cam bien `temperature`, `humidity`, `light` va luu vao bang `monitoring.SensorReading`.
- Nhan JSON trang thai ESP32 va luu trang thai moi nhat trong bo nho.
- Nhan frame binary audio tu ESP32 va dem so chunk da nhan. Speech-to-text chua duoc xu ly trong code hien tai.
- API xem trang thai ESP32: `GET /api/esp32/`.
- API gui lenh xuong ESP32: `POST /api/esp32/commands/`.
- API phan tich y dinh dieu khien tu text: `POST /api/llm/intent/`.
- Dashboard server-rendered tai `/dashboard/`.
- Django Admin cho sensor readings, output targets, command logs va automation rules.
- Ho tro SQLite khi chay local va PostgreSQL/Supabase/Render thong qua `DATABASE_URL`.
- Ho tro deploy voi Daphne, WhiteNoise va Django Channels in-memory channel layer.

## Trang thai trien khai

Da co trong code:

- WebSocket gateway ESP32.
- Luu du lieu cam bien.
- Gui lenh `set_output` den ESP32.
- Log lenh dieu khien.
- Dashboard doc du lieu.
- Parse intent bang Groq.
- Model luu automation rule.

Chua co trong code hien tai:

- Speech-to-text tu audio binary.
- Text-to-speech gui audio ve ESP32.
- Rule engine tu dong quet `AutomationRule` va kich hoat lenh.
- ACK hoan tat lenh tu ESP32.
- Redis/multi-server channel layer.

## Kien truc tong quan

```text
ESP32
  |-- WebSocket JSON sensor/status/ping --> Django Channels
  |-- WebSocket binary audio -----------> Django Channels
  <-- WebSocket command JSON ------------ Django Channels

Django project VDK
  |-- gateway       Ket noi WebSocket va trang thai ESP32
  |-- monitoring    Luu lich su cam bien
  |-- control       Cau hinh output va log lenh
  |-- automation    Luu rule tu dong dang JSON
  |-- assistant_ai  Goi Groq de parse intent tu text
  |-- dashboard     Giao dien web doc du lieu server-side

Database
  |-- SQLite mac dinh khi local
  |-- PostgreSQL neu co DATABASE_URL
```

## Cong nghe

- Python, Django 5.2
- Django Channels 4
- Daphne ASGI server
- Groq Python SDK
- SQLite hoac PostgreSQL
- dj-database-url
- python-dotenv
- WhiteNoise

## Cau truc thu muc

```text
VDK/
  settings.py       Cau hinh Django, database, Channels, static files
  urls.py           Route goc: home, admin, dashboard, api
  api_urls.py       Gom cac API app-level
  asgi.py           ASGI entrypoint cho HTTP va WebSocket
  wsgi.py           WSGI entrypoint

gateway/
  consumers.py      ESP32 WebSocket consumer
  protocol.py       Trang thai ket noi ESP32 va hang so message
  routing.py        Route WebSocket /ws/esp32/
  views.py          API GET /api/esp32/
  urls.py           Gateway API routes
  tests.py          Test WebSocket va command API

monitoring/
  models.py         SensorReading
  admin.py          Django Admin cho SensorReading

control/
  models.py         OutputTarget, CommandLog
  views.py          API gui command
  urls.py           Route /api/esp32/commands/
  admin.py          Admin output va command

automation/
  models.py         AutomationRule
  admin.py          Admin automation rule

assistant_ai/
  services.py       Groq client, parse JSON intent, rate limit
  views.py          API POST /api/llm/intent/
  urls.py           Route LLM intent API
  tests.py          Test intent API va parser

dashboard/
  views.py          Cac trang overview, sensors, controls, commands, rules
  urls.py           Dashboard routes
  templates/        HTML template va CSS inline

docs/
  api_payloads.md
  database.md
  esp32_websocket_protocol.md
  structure.md
```

## Cai dat local

Tao virtual environment va cai thu vien:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Tao database local:

```powershell
python manage.py migrate
```

Tao admin user neu can vao `/admin/`:

```powershell
python manage.py createsuperuser
```

Chay server local bang Daphne de ho tro WebSocket:

```powershell
daphne -b 127.0.0.1 -p 8000 VDK.asgi:application
```

Co the dung server dev cua Django cho HTTP, nhung nen dung Daphne khi test WebSocket:

```powershell
python manage.py runserver
```

## Bien moi truong

Du an doc file `.env` o thu muc goc.

```text
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=<secret-key>
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=

DATABASE_URL=

LLM_PROVIDER=groq
GROQ_API_KEY=<groq-api-key>
GROQ_MODEL=llama-3.1-8b-instant
LLM_MIN_REQUEST_INTERVAL_SECONDS=2
```

Ghi chu:

- Neu khong co `DATABASE_URL`, Django dung SQLite file `db.sqlite3`.
- Neu deploy voi PostgreSQL/Supabase/Render, gan `DATABASE_URL`.
- `GROQ_API_KEY` bat buoc neu goi `/api/llm/intent/`.
- `DJANGO_ALLOWED_HOSTS` can them IP may tinh khi ESP32 ket noi qua LAN.

## URL chinh

```text
GET  /                         Redirect den /dashboard/
GET  /admin/                   Django Admin
GET  /dashboard/               Overview
GET  /dashboard/sensors/       100 ban ghi cam bien moi nhat
GET  /dashboard/controls/      Danh sach output
GET  /dashboard/commands/      100 command moi nhat
GET  /dashboard/rules/         Automation rules

GET  /api/esp32/               Trang thai ESP32 trong bo nho
POST /api/esp32/commands/      Gui command xuong ESP32
POST /api/llm/intent/          Parse cau lenh text thanh intent JSON

WS   /ws/esp32/                WebSocket cho ESP32
```

## WebSocket ESP32

Endpoint local:

```text
ws://127.0.0.1:8000/ws/esp32/
```

Endpoint LAN vi du:

```text
ws://192.168.1.10:8000/ws/esp32/
```

### ESP32 gui du lieu cam bien

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

Server se:

1. Cap nhat `latest_sensor` trong memory.
2. Tao ban ghi `SensorReading`.
3. Khong gui ACK.

### ESP32 gui trang thai

```json
{
  "type": "status",
  "wifi_rssi": -55,
  "free_heap": 182340
}
```

Server se cap nhat `last_status` trong memory va khong gui ACK.

### ESP32 gui heartbeat

Request:

```json
{
  "type": "ping"
}
```

Response:

```json
{
  "type": "pong"
}
```

### ESP32 gui audio

ESP32 gui binary WebSocket frames. Code hien tai chi tang `audio_chunks_received`, chua xu ly speech-to-text.

### Server gui command ve ESP32

ESP32 se nhan message dang:

```json
{
  "type": "command",
  "command_id": "generated-command-id",
  "name": "set_output",
  "params": {
    "target": "led",
    "state": true
  }
}
```

## API

### `GET /api/esp32/`

Tra ve trang thai ESP32 dang luu trong memory:

```json
{
  "connected": true,
  "last_seen": 1780000000.123,
  "latest_sensor": {
    "temperature": 30.5,
    "humidity": 70.2,
    "light": 410
  },
  "last_status": {
    "type": "status",
    "wifi_rssi": -55
  },
  "audio_chunks_received": 3
}
```

### `POST /api/esp32/commands/`

Gui command xuong ESP32 qua channel group `esp32`.

Body bat den:

```json
{
  "name": "set_output",
  "params": {
    "target": "led",
    "state": true
  }
}
```

Body tat quat:

```json
{
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": false
  }
}
```

Response thanh cong:

```json
{
  "queued": true,
  "command_id": "generated-command-id",
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": false
  }
}
```

HTTP status thanh cong la `202`.

Server dong thoi:

- Gui command qua WebSocket group.
- Tao `CommandLog` voi status `sent`.
- Neu `name` la `set_output` va `params.target` trung `OutputTarget.key` dang enabled, cap nhat `OutputTarget.current_state`.

### `POST /api/llm/intent/`

Parse text thanh intent dieu khien.

Body:

```json
{
  "text": "Troi nong qua"
}
```

Response vi du:

```json
{
  "action": "turn_on",
  "device": "fan",
  "reply_message": "Da bat quat."
}
```

Gia tri hop le:

```text
action: turn_on, turn_off, get_status
device: light, fan, sensor
reply_message: chuoi ngan, khong rong
```

Mapping de gui command:

```text
device fan   -> params.target fan
device light -> params.target led
turn_on      -> state true
turn_off     -> state false
get_status   -> doc trang thai cam bien, chua la command xuong ESP32
```

Loi thuong gap:

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

## Database models

### `monitoring.SensorReading`

Luu lich su du lieu moi truong.

```text
temperature  Float, nullable
humidity     Float, nullable
light        Float, nullable
raw_data     JSON payload goc tu ESP32
created_at   Thoi diem tao
```

Mac dinh sap xep moi nhat truoc.

### `control.OutputTarget`

Luu cac ngo ra co the dieu khien.

```text
key            Slug unique, vi du led, fan
name           Ten hien thi
kind           Loai output, vi du light, fan, relay
current_state  JSON trang thai hien tai
is_enabled     Cho phep cap nhat/dieu khien
created_at
updated_at
```

Vi du nen tao trong Admin:

```text
key=led, name=LED, kind=light
key=fan, name=Fan, kind=fan
```

### `control.CommandLog`

Luu lich su lenh server gui den ESP32.

```text
command_id    ID sinh tu uuid hex
name          Ten lenh, vi du set_output
target        Target lay tu params.target
params        JSON params
source        manual, voice, rule, system
status        queued, sent, failed, completed
created_at
sent_at
completed_at
```

API hien tai luu command voi `source=manual` va `status=sent`.

### `automation.AutomationRule`

Luu rule tu dong dang JSON de phuc vu rule engine sau nay.

```text
name
description
conditions
action
is_enabled
created_at
updated_at
```

Vi du:

```json
{
  "conditions": [
    {
      "field": "light",
      "operator": "<",
      "value": 300
    }
  ],
  "action": {
    "name": "set_output",
    "params": {
      "target": "led",
      "state": true
    }
  }
}
```

## Dashboard va Admin

Dashboard:

- `/dashboard/`: tong quan ket noi ESP32, chi so moi nhat, so luong row/output/command/rule.
- `/dashboard/sensors/`: 100 ban ghi cam bien moi nhat.
- `/dashboard/controls/`: output targets va current state.
- `/dashboard/commands/`: 100 command moi nhat.
- `/dashboard/rules/`: automation rules.

Admin:

- Tao `OutputTarget` truoc khi test lenh `led` hoac `fan`.
- Xem `SensorReading` de kiem tra ESP32 da gui du lieu.
- Xem `CommandLog` de kiem tra server da gui lenh.
- Tao `AutomationRule` de chuan bi cho rule engine.

## Demo nhanh bang PowerShell

Kiem tra trang thai ESP32:

```powershell
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8000/api/esp32/
```

Parse cau lenh text:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/llm/intent/ `
  -ContentType application/json `
  -Body '{"text":"Troi nong qua"}'
```

Gui lenh bat LED:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/esp32/commands/ `
  -ContentType application/json `
  -Body '{"name":"set_output","params":{"target":"led","state":true}}'
```

Gui lenh bat quat:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/esp32/commands/ `
  -ContentType application/json `
  -Body '{"name":"set_output","params":{"target":"fan","state":true}}'
```

## Luong demo giong noi

1. Nguoi dung noi vao microphone tren ESP32.
2. ESP32 gui audio binary len WebSocket.
3. Phan speech-to-text chua co trong code, nen demo hien tai dung text transcript gui vao `/api/llm/intent/`.
4. Groq tra ve intent JSON.
5. Backend hoac client demo doi intent thanh command:
   - `fan` -> target `fan`
   - `light` -> target `led`
   - `turn_on` -> `state=true`
   - `turn_off` -> `state=false`
6. Goi `/api/esp32/commands/`.
7. ESP32 nhan command qua WebSocket va dieu khien chan output.

## Test

Chay test:

```powershell
python manage.py test
```

Test hien co bao gom:

- WebSocket nhan sensor data va luu database.
- WebSocket nhan binary audio va tang counter.
- WebSocket ping/pong.
- API command tao `CommandLog` va cap nhat `OutputTarget`.
- API intent validate input va xu ly loi Groq API key.
- Parser JSON LLM response.
- Rate limit giua cac request LLM.

## Deploy Render

Build command:

```bash
bash build.sh
```

Start command:

```bash
python manage.py migrate && daphne -b 0.0.0.0 -p $PORT VDK.asgi:application
```

Bien moi truong khuyen nghi:

```text
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<strong-secret>
DJANGO_ALLOWED_HOSTS=<render-domain>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<render-domain>
DATABASE_URL=<postgres-url>
LLM_PROVIDER=groq
GROQ_API_KEY=<groq-key>
GROQ_MODEL=llama-3.1-8b-instant
LLM_MIN_REQUEST_INTERVAL_SECONDS=2
```

Neu Render co `RENDER_EXTERNAL_HOSTNAME`, settings se tu them vao `ALLOWED_HOSTS`. Neu co `RENDER_EXTERNAL_URL`, settings se tu them vao `CSRF_TRUSTED_ORIGINS`.

## Luu y ve Channels

Du an dang dung `channels.layers.InMemoryChannelLayer`. Cach nay phu hop demo:

```text
1 Django/Daphne process
1 ESP32 board
1 dashboard
khong can Redis
```

Neu deploy nhieu instance/process hoac can scale production, can doi sang Redis channel layer de message group WebSocket duoc chia se giua cac process.

