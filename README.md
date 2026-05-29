# ttygeist

![Maintenance](https://img.shields.io/maintenance/yes/2026.svg)

## Overview

ttygeist manages a single serial connection allowing both human and
llm access via mcp. The server is accessible via http(s) or stdio. The
human component has minimal interference and basic ansi code
support. Serial output will be (line) buffered even if a human or llm
is not directly interacting with the serial device.

Devices are discovered automatically via USB enumeration. An accept
list pins serial numbers to friendly names with optional per-device
overrides. A block list excludes unwanted ports.

## Installation

[uv](https://docs.astral.sh/uv/) is recommended.

```bash
cd ~/src/otakup0pe-ttygeist
uv sync
uv run ttygeist            # STDIO mode (default)
uv run ttygeist --transport http   # HTTP(S) mode
```

## Configuration

ttygeist loads config from a YAML file passed via `--config` (default:
`config.yaml`). No config file is required -- with no config, ttygeist
discovers all serial ports and auto-names them.

### Minimal Config (accept list)

Name specific devices by USB serial number:

```yaml
devices:
  devboard: "AB:CD:EF:12:34:56"
  radio: "78:90:AB:CD:EF:12"
```

### Extended Config

Per-device overrides and block list:

```yaml
devices:
  devboard:
    serial: "AB:CD:EF:12:34:56"
    baud: 460800

  radio: "78:90:AB:CD:EF:12"

  # Non-USB device (explicit port path)
  gps:
    port: "/dev/ttyS1"
    baud: 9600

# Ignore specific serial numbers
block:
  - "AA:BB:CC:DD:EE:FF"

# Override global defaults
defaults:
  baud: 115200
  dtr: true
  rts: false

# Disable tools you don't need
disabled_tools:
  - server_shutdown

# Firmware identification (optional, regex patterns)
firmware_patterns:
  - pattern: "Version:\\s*(.+)"
    field: firmware_version
  - pattern: "MyDevice Boot"
    product: mydevice

buffer:
  max_size_mb: 10
  line_limit: 100000

server:
  name: ttygeist
  transport: stdio

logging:
  target: journal          # "journal" (systemd) or "file"
  file: ttygeist.log       # only used when target=file
  level: INFO
```

Short form (`name: "serial_number"`) and extended form (`name: {serial: "...", baud: ...}`) can be mixed freely.

### Chipset Defaults

ttygeist attempts sensible configuration based on USB chipset
(VID/PID). Per-device config overrides these:

| Chipset | VID:PID | Default baud | DTR | RTS |
|---------|---------|-------------|-----|-----|
| Espressif USB-JTAG | 303a:1001 | 115200 | on | off |
| CH340/CH341 | 1a86:7523 | 115200 | on | on |
| CP2102/CP2104 | 10c4:ea60 | 115200 | on | on |
| FTDI FT232R | 0403:6001 | 115200 | on | on |

### Environment Variable Overrides

| Variable | Overrides | Notes |
|---|---|---|
| `TTYGEIST_API_KEYS` | `auth.api_keys` | Comma-separated; appended to file keys |
| `TTYGEIST_AUTH_HEADER` | `auth.header_name` | `X-API-Key` or `Authorization` |
| `TTYGEIST_ALLOW_ANON` | `auth.allow_anon` | `1`, `true`, or `yes` |
| `TTYGEIST_REQUEST_LOG` | `logging.request_log` | `1`, `true`, or `yes` |
| `TTYGEIST_TRANSPORT` | `server.transport` | `http` or `stdio` |

### Setup Workflow

1. Plug in devices
2. Start ttygeist with no config
3. Call `serial_status` -- shows all discovered devices with serial numbers
4. Copy serial numbers into config, assign friendly names
5. Restart ttygeist -- devices matched by serial number, named as configured

## Authentication

HTTP transport requires API keys by default. Configure in
`auth.api_keys` or via `TTYGEIST_API_KEYS`. Supports `X-API-Key` and
`Authorization: Bearer` headers. Set `allow_anon: true` for local dev
only.

STDIO transport does not use authentication.

## CLI Usage

```bash
ttygeist-cli status           # connection and buffer stats
ttygeist-cli show -n 50       # last 50 buffered lines
ttygeist-cli tail -n 20       # live tail
ttygeist-cli terminal -n 10   # interactive terminal (exit: Ctrl+])
```

The CLI shares serial access with MCP clients. Output is visible to
both simultaneously. The CLI is only accessible on the host ttygeist
is running on.

## MCP Tools

All tools accept an optional `device` parameter. When only one device is connected, `device` can be omitted.

### serial_status

Get device status. With no `device` argument and multiple devices
connected, returns an overview of all devices including port,
connection state, USB info, and firmware identification.

### serial_read

Read buffered lines by count (`lines`) or time window
(`duration_seconds`). Optional `clear_after_read`.

### serial_write

Write data to a device. Escape sequences (`\x03`, `\n`) are decoded
from JSON-RPC strings automatically. Optional `add_newline` (default:
true).

### buffer_inspect

Non-destructive tail view of recent lines (`tail_lines` parameter, default 50).

### buffer_clear

Remove all buffered lines for a device. Returns count cleared.

### serial_control

Control a device's serial connection:
- `reconnect` -- force disconnect + reconnect
- `suspend` -- release the port for external tools (e.g. esptool
  flash). Server stays running, buffer preserved, auto-reconnect
  disabled.
- `resume` -- re-acquire port after external tool use. Background scan
  picks up any port path changes.

### server_shutdown

Cooperative shutdown request.

## Logging

Default logging target is systemd journal (`python-systemd`
JournalHandler). Falls back to file logging if `python-systemd` is not
installed. Set `logging.target: file` to use file logging explicitly.

## Transport Modes

- **STDIO** (default): MCP over stdin/stdout. No auth needed.
- **HTTP(S)**: TLS required. Configure cert/key in `server.tls_cert` / `server.tls_key`.

## License

Licensed under the BSD License. See [LICENSE](https://github.com/otakup0pe/ttygeist/blob/mainline/LICENSE).

## Feedback, bug-reports, requests, ...

Are [Welcome](https://github.com/otakup0pe/ttygeist/issues)!

## Author

The `ttygeist` tool was created by [Jonathan
Freedman](https://jonathanfreedman.bio/) to facilitate testing just
how many ESP32 devices he seems to be constantly surrounded by. This
tool was created with LLM assistance.
