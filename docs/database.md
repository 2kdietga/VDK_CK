# Database Design

The project is split by responsibility:

```text
gateway      WebSocket connection with the single ESP32 board
monitoring   sensor readings and environment history
control      outputs such as led/fan and command logs
automation   automatic rules
```

## monitoring.SensorReading

Stores historical environment data from ESP32.

Fields:

```text
temperature
humidity
light
raw_data
created_at
```

`raw_data` keeps the original ESP32 payload so new sensors can be added later without changing schema immediately.

## control.OutputTarget

Stores controllable outputs connected to ESP32.

Examples:

```text
key=led, name=LED, kind=light
key=fan, name=Fan, kind=fan
```

Commands use `params.target` to match `OutputTarget.key`.

## control.CommandLog

Stores every command sent from the server to ESP32.

Important fields:

```text
command_id
name
target
params
source
status
created_at
sent_at
completed_at
```

Current command status is usually `sent` because the protocol does not require ACK.

## automation.AutomationRule

Stores rule-engine definitions.

Example condition/action shape:

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

The rule shape stays in JSON while the rule engine is still evolving.

