# AIoT Monitor - ESP32, Django Channels, Voice AI va Automation Rules

Backend Django cho he thong AIoT dung ESP32 de giam sat moi truong va dieu khien LED/quat. He thong nhan du lieu cam bien realtime qua WebSocket, hien thi dashboard, gui command nguoc ve ESP32, xu ly cau lenh giong noi/text bang LLM, va tu dong kich hoat command theo automation rule.

## Tinh nang

- WebSocket cho 1 ESP32 tai `/ws/esp32/`.
- Nhan du lieu cam bien `temperature`, `humidity`, `light` realtime.
- Luu sensor history theo cua so trung binh 180 giay.
- Gui command `set_output` dieu khien `led` va `fan`.
- Dong bo trang thai LED/fan moi nhat cho ESP32 moi ket noi.
- ACK command tu ESP32 de cap nhat `CommandLog` va `OutputTarget.current_state`.
- Canh bao nhiet do thong minh dua tren nguong hien tai va lich su nhiet do gan day.
- Dashboard server-rendered:
  - Overview
  - Sensors
  - Controls
  - Commands
  - Rules
- Automation rule engine:
  - Dieu kien 1 hoac 2 condition AND.
  - Vi du `temperature > 30 AND temperature < 50`.
  - Cooldown de tranh spam command.
  - Tu disable rule cu khi rule moi xung dot.
- API LLM intent `POST /api/llm/intent/`:
  - Dieu khien ngay: bat/tat den, bat/tat quat, set muc phan tram.
  - Hoi thong tin hien tai: nhiet do, do am, anh sang, trang thai den/quat.
  - Tao/sua/bat/tat/xoa automation rule bang ngon ngu tu nhien.
- Voice flow qua WebSocket:
  - ESP32 gui audio PCM binary.
  - Server speech-to-text bang `SpeechRecognition`.
  - Server parse intent bang Groq.
  - Server gui command/rule va text-to-speech audio ve ESP32 bang VoiceRSS hoac fallback tone.
- Ho tro SQLite local va PostgreSQL qua `DATABASE_URL`.
- Deploy duoc voi Daphne, WhiteNoise, Django Channels.

## Kien truc

```text
ESP32
  -> WebSocket JSON sensor/status/ping/audio_end
  -> WebSocket binary PCM audio
  <- WebSocket command JSON
  <- WebSocket binary PCM TTS audio

Django ASGI
  gateway       WebSocket ESP32, audio, command ACK
  monitoring    SensorReading
  control       OutputTarget, CommandLog, command API
  automation    AutomationRule va rule engine
  assistant_ai  Groq intent parser va IoT context
  dashboard     Web UI
```

## Cong nghe

- Python 3.10+
- Django 5.2
- Django Channels 4
- Daphne
- Groq Python SDK
- SpeechRecognition
- VoiceRSS TTS
- SQLite hoac PostgreSQL
- WhiteNoise

## Cai dat local

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
```

Tao admin user neu can:

```powershell
python manage.py createsuperuser
```

Chay server:

```powershell
python manage.py runserver 0.0.0.0:8000
```

Hoac chay Daphne:

```powershell
daphne -b 0.0.0.0 -p 8000 VDK.asgi:application
```

## Bien moi truong

Du an doc `.env` o thu muc goc.

```text
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=<secret>
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,<LAN-IP>
DJANGO_CSRF_TRUSTED_ORIGINS=

DATABASE_URL=

LLM_PROVIDER=groq
GROQ_API_KEY=<groq-api-key>
GROQ_MODEL=llama-3.1-8b-instant
LLM_MIN_REQUEST_INTERVAL_SECONDS=2

SPEECH_RECOGNITION_LANGUAGE=vi-VN

VOICERSS_API_KEY=<voicerss-api-key>
VOICERSS_LANGUAGE=vi-vn
VOICERSS_VOICE=Chi
TTS_VOLUME_GAIN=1.8
```

Ghi chu:

- Khong co `DATABASE_URL` thi dung SQLite `db.sqlite3`.
- Goi `/api/llm/intent/` can `GROQ_API_KEY`.
- Voice TTS can `VOICERSS_API_KEY`; neu thieu, server gui fallback tone.
- ESP32 ket noi LAN can them IP may tinh vao `DJANGO_ALLOWED_HOSTS`.

## URL chinh

```text
GET  /                         Redirect /dashboard/
GET  /admin/                   Django Admin

GET  /dashboard/               Overview
GET  /dashboard/sensors/       Sensor readings
GET  /dashboard/controls/      LED/fan controls
GET  /dashboard/commands/      Command logs
GET  /dashboard/rules/         Automation rules UI

GET  /api/esp32/               In-memory ESP32 state
POST /api/esp32/commands/      Send manual command
GET  /api/esp32/commands/<id>/ Command status
POST /api/llm/intent/          Parse/apply text intent

WS   /ws/esp32/                ESP32 WebSocket
```

## WebSocket ESP32

Endpoint:

```text
ws://127.0.0.1:8000/ws/esp32/
ws://<LAN-IP>:8000/ws/esp32/
```

### Sensor data

ESP32 gui:

```json
{
  "type": "sensor_data",
  "data": {
    "temperature": 31.5,
    "humidity": 70,
    "light": 420
  }
}
```

Server se:

- Cap nhat latest sensor trong RAM.
- Them sample vao chart realtime.
- Luu `SensorReading` theo cua so trung binh 180 giay.
- Evaluate automation rules.
- Neu rule match, gui command ve ESP32 va log `CommandLog.source=rule`.
- Evaluate canh bao nhiet do tu du lieu hien tai + lich su database.
- Neu canh bao match, gui `stop_listen`, audio canh bao PCM ve ESP32, roi gui `audio_done`.

### Temperature alert audio

Khi nhiet do co rui ro cao, server gui audio canh bao ve ESP32 theo cung co che audio cua voice:

```text
ESP32 -> sensor_data
Server -> stop_listen
Server -> binary PCM audio warning
Server -> audio_done
```

Thong diep canh bao duoc tao bang VoiceRSS TTS. Neu VoiceRSS loi hoac thieu API key, server gui fallback tone de ESP32 van phat duoc am bao.

Trang thai canh bao hien tai nam trong `/api/esp32/`:

```json
{
  "alert": {
    "active_key": "temperature",
    "level": "warning",
    "last_alert_at": 1710000000.0
  }
}
```

### Status

```json
{
  "type": "status",
  "wifi_rssi": -55,
  "free_heap": 182340
}
```

### Ping

```json
{"type": "ping"}
```

Server tra:

```json
{"type": "pong"}
```

### Server command

ESP32 nhan:

```json
{
  "type": "command",
  "command_id": "generated-id",
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true,
    "value": 80
  }
}
```

Sau khi chay command, ESP32 phai ACK:

```json
{
  "type": "command_ack",
  "command_id": "generated-id",
  "status": "completed",
  "params": {
    "target": "fan",
    "state": true,
    "value": 80
  }
}
```

ACK `completed` se cap nhat `CommandLog.status=completed` va `OutputTarget.current_state`.

### Voice audio

ESP32 gui PCM 16-bit mono 16 kHz bang binary WebSocket frames, sau do gui:

```json
{"type": "audio_end"}
```

Server se:

1. Ghep PCM thanh WAV.
2. Speech-to-text bang Google SpeechRecognition.
3. Parse intent bang Groq.
4. Gui command hoac apply automation rule.
5. Gui `stop_listen`.
6. Gui PCM TTS audio binary.
7. Gui `audio_done`.

## API Command

### `POST /api/esp32/commands/`

Bat quat 80%:

```json
{
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true,
    "value": 80
  }
}
```

Tat den:

```json
{
  "name": "set_output",
  "params": {
    "target": "led",
    "state": false,
    "value": 0
  }
}
```

Response `202`:

```json
{
  "queued": true,
  "awaiting_ack": true,
  "command_id": "generated-id",
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true,
    "value": 80
  }
}
```

## API LLM Intent

### Dieu khien ngay

Request:

```json
{
  "text": "bat quat 80 phan tram"
}
```

Response:

```json
{
  "commands": [
    {
      "action": "turn_on",
      "device": "fan",
      "value": 80
    }
  ],
  "automation_rules": [],
  "reply_message": "Da bat quat muc 80 phan tram.",
  "action": "turn_on",
  "device": "fan",
  "value": 80
}
```

Ghi chu: API parse intent khong tu gui command manual xuong ESP32. Voice WebSocket thi co gui command. Neu test bang HTTP, client co the lay `commands` va goi `/api/esp32/commands/`.

### Hoi thong tin hien tai

Request:

```json
{
  "text": "do am hien tai bao nhieu"
}
```

Server gui context hien tai cho LLM va co lop override cau tra loi tu du lieu that:

```json
{
  "reply_message": "Do am hien tai la 65%."
}
```

Context gom:

- ESP32 connected/last_seen.
- Sensor moi nhat tu RAM, fallback database.
- Trang thai `led` va `fan` trong `OutputTarget.current_state`.

### Tao automation rule

Request:

```json
{
  "text": "khi nhiet do tren 30 thi bat quat 80 phan tram"
}
```

Response:

```json
{
  "commands": [],
  "automation_rules": [
    {
      "operation": "create",
      "name": "Bat quat khi nhiet do tren 30",
      "condition": {
        "field": "temperature",
        "operator": ">",
        "value": 30
      },
      "action": {
        "target": "fan",
        "state": true,
        "value": 80,
        "cooldown_seconds": 60
      }
    }
  ],
  "reply_message": "Da tao rule bat quat 80 phan tram khi nhiet do tren 30 do.",
  "automation_applied": true,
  "automation_results": [
    "Created automation rule: Bat quat khi nhiet do tren 30."
  ]
}
```

`/api/llm/intent/` se apply `automation_rules` vao database ngay.

### Tao rule khoang

Request:

```json
{
  "text": "khi nhiet do tren 30 va duoi 50 thi bat quat 80 phan tram"
}
```

Rule se duoc luu voi 2 condition AND:

```json
[
  {"field": "temperature", "operator": ">", "value": 30},
  {"field": "temperature", "operator": "<", "value": 50}
]
```

Rule chi match khi `temperature > 30 AND temperature < 50`.

### Sua, bat, tat, xoa rule bang voice/text

Vi du:

```text
doi rule bat quat khi nong thanh nhiet do tren 32 va quat 90 phan tram
tat rule bat quat khi nong
bat lai rule bat quat khi nong
xoa rule bat quat khi nong
```

LLM nen tra `automation_rules[].operation` la `update`, `disable`, `enable`, hoac `delete`.

## Automation Rules

Model:

```text
name
description
conditions  JSON list
action      JSON object
is_enabled
created_at
updated_at
```

Condition hop le:

```json
{"field": "temperature", "operator": ">", "value": 30}
{"field": "humidity", "operator": "<=", "value": 80}
{"field": "light", "operator": "<", "value": 300}
```

Operator hop le:

```text
> >= < <= == = !=
```

Action hop le:

```json
{
  "name": "set_output",
  "params": {
    "target": "fan",
    "state": true,
    "value": 80
  },
  "cooldown_seconds": 60
}
```

Conflict handling:

- Khi tao/update rule moi, server tim rule cu dang enabled co cung target.
- Neu condition giao nhau va action khac nhau, rule cu bi disable.
- Rule moi la rule thang.
- Rule cu khong bi xoa de nguoi dung xem/sua lai.

Vi du conflict:

```text
Rule cu: temperature > 30 -> fan on 80
Rule moi: temperature > 35 -> fan off 0
```

Hai rule cung xay ra khi `temperature > 35`, action trai nhau, nen rule cu bi disable.

Voi rule khoang:

```text
Rule cu: temperature > 30 AND temperature < 50 -> fan on
Rule moi: temperature > 60 -> fan off
```

Khong conflict vi hai khoang khong giao nhau.

## Temperature Alert Algorithm

Co che canh bao nhiet do nam o `monitoring/alerts.py`. Muc tieu la khong chi canh bao theo nguong cung, ma con nhin vao du lieu qua khu trong database de phat hien nhiet do tang bat thuong so voi moi truong gan day.

Moi lan ESP32 gui `sensor_data`, server lay:

- `T`: nhiet do hien tai tu packet ESP32.
- `H`: 10 ban ghi `SensorReading` moi nhat trong database.
- `avg(H)`: nhiet do trung binh gan day.
- `latest(H)`: nhiet do moi nhat trong lich su.

Server tinh `risk_score` theo cong thuc cong diem:

```text
risk_score = absolute_threshold_score
           + average_delta_score
           + trend_delta_score
```

### 1. Diem nguong tuyet doi

```text
Neu T >= 40 do C: +70 diem
Neu 35 <= T < 40 do C: +40 diem
Neu T < 35 do C: +0 diem
```

Y nghia:

- Tu 35 do C tro len la nong, can co kha nang warning.
- Tu 40 do C tro len la vung nguy hiem, gan cham muc critical neu co them dau hieu tang nhanh.

### 2. Diem lech so voi trung binh gan day

Chi tinh khi database co lich su nhiet do.

```text
average_delta = T - avg(H)

Neu average_delta >= 5: +30 diem
Neu average_delta >= 3: +15 diem
Neu average_delta < 3:  +0 diem
```

Y nghia: cung la 35 do C, nhung neu truoc do phong chi quanh 29-30 do C thi day la bien dong bat thuong va can canh bao manh hon.

### 3. Diem xu huong tang nhanh

Chi tinh khi database co lich su nhiet do.

```text
trend_delta = T - latest(H)

Neu trend_delta >= 4: +25 diem
Neu trend_delta >= 2: +10 diem
Neu trend_delta < 2:  +0 diem
```

Y nghia: neu nhiet do vua tang nhanh so voi lan doc gan nhat thi rui ro cao hon, vi co the dang co nguon nhiet bat thuong.

### 4. Phan loai muc canh bao

```text
Neu risk_score >= 80: critical
Neu risk_score >= 40: warning
Neu risk_score < 40:  khong canh bao
```

Vi du:

```text
T = 36, H rong
risk_score = 40
=> warning
```

```text
T = 35, avg(H) = 29.5, latest(H) = 29
risk_score = 40 + 30 + 25 = 95
=> critical
```

```text
T = 41, avg(H) = 30, latest(H) = 30
risk_score = 70 + 30 + 25 = 125
=> critical
```

### 5. Cooldown va escalation

De tranh loa ESP32 bi spam lien tuc, server dung cooldown:

```text
ALERT_COOLDOWN_SECONDS = 60
```

Neu canh bao cung muc lap lai trong 60 giay, server khong gui lai audio. Tuy nhien neu muc canh bao tang tu `warning` len `critical`, server gui ngay lap tuc, khong doi cooldown.

```text
should_alert = escalated OR cooldown_elapsed
```

Trong do:

- `escalated`: muc moi cao hon muc canh bao truoc do.
- `cooldown_elapsed`: da qua it nhat 60 giay tu lan canh bao gan nhat.

### 6. Dieu kien recovery

Khi nhiet do ha ve nguong an toan:

```text
TEMPERATURE_RECOVERY_THRESHOLD = 33 do C
```

Neu khong con level canh bao va `T <= 33`, server xoa trang thai canh bao trong RAM:

```text
alert_active_key = None
alert_level = None
```

### Pseudocode

```python
history = SensorReading.objects.order_by('-created_at')[:10]
risk_score = threshold_score(T) + average_score(T, history) + trend_score(T, history)
level = 'critical' if risk_score >= 80 else 'warning' if risk_score >= 40 else None

if level and (level_escalated(level) or cooldown_elapsed(60)):
    send_warning_audio_to_esp32(level, risk_score)
```

## Dashboard

- `/dashboard/overview/` khong dung; overview nam tai `/dashboard/`.
- `/dashboard/sensors/`: bang readings va chart lich su.
- `/dashboard/controls/`: nut bat/tat va slider LED/fan.
- `/dashboard/commands/`: command log dang de doc, khong hien JSON raw.
- `/dashboard/rules/`: tao/sua/xoa rule truc quan:
  - 1 hoac 2 condition AND.
  - Chon sensor, operator, threshold.
  - Chon device, action, level, cooldown.

## Database

### `monitoring.SensorReading`

```text
temperature
humidity
light
raw_data
created_at
```

### `control.OutputTarget`

```text
key            led | fan
name
kind
current_state  {"target": "...", "state": true, "value": 80}
is_enabled
created_at
updated_at
```

Migration mac dinh tao `led` va `fan`.

### `control.CommandLog`

```text
command_id
name
target
params
source        manual | voice | rule | system
status        queued | sent | failed | completed
sent_at
completed_at
```

### `automation.AutomationRule`

```text
conditions    list condition AND
action        set_output action
is_enabled
```

## Demo nhanh PowerShell

Trang thai ESP32:

```powershell
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8000/api/esp32/
```

Gui command bat quat:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/esp32/commands/ `
  -ContentType application/json `
  -Body '{"name":"set_output","params":{"target":"fan","state":true,"value":80}}'
```

Tao rule bang LLM:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/llm/intent/ `
  -ContentType application/json `
  -Body '{"text":"khi nhiet do tren 30 va duoi 50 thi bat quat 80 phan tram"}'
```

## Test

```powershell
python manage.py test
```

Hoac neu test database PostgreSQL con ton tai sau khi bi interrupt:

```powershell
python manage.py test --keepdb
```

Test hien co bao gom:

- WebSocket sensor/status/ping/audio.
- Sync LED/fan khi ESP32 moi ket noi.
- Command API va ACK.
- LLM parser commands, multi-command, context reply.
- Voice automation rule create/update/enable/disable/delete.
- Range rules 2 conditions.
- Rule conflict handling.
- Dashboard rules CRUD.

## Deploy Render

Build command:

```bash
bash build.sh
```

Start command:

```bash
python manage.py migrate && daphne -b 0.0.0.0 -p $PORT VDK.asgi:application
```

Bien moi truong deploy:

```text
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<strong-secret>
DJANGO_ALLOWED_HOSTS=<render-domain>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<render-domain>
DATABASE_URL=<postgres-url>
GROQ_API_KEY=<groq-key>
VOICERSS_API_KEY=<voicerss-key>
```

## Luu y production

Du an dang dung `channels.layers.InMemoryChannelLayer`, phu hop demo 1 process. Neu chay nhieu instance/process, can doi sang Redis channel layer de group WebSocket hoat dong dung.
