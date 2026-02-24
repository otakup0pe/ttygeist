# ttygeist

![Maintenance](https://img.shields.io/maintenance/yes/2026.svg)

## Overview

ttygeist manages a single serial connection allowing both human and llm access via mcp. the server is accessible via http(s) or stdio. the human component has minimal interference and basic ansi code support. serial output will be (line) buffered even if a human or llm is not directly interacting with the serial device.

## Installation

The use of [uv](https://docs.astral.sh/uv/) is recommended because it is the bees knees.

```bash
$ cd ~/src/otakup0pe-ttygeist
$ uv sync
$ uv run ttygeist            # STDIO mode (default)
$ uv run ttygeist -- --transport http   # HTTP(S) mode
```

Environment override alternative:
```bash
$ export TTYGEIST_TRANSPORT=http
$ uv run ttygeist
```

## Configuration

ttygeist loads configuration from a YAML file passed via `--config` (or `-c`). When no file is provided, sensible defaults are used for every key -- no config file is required to get started.

### Config Reference

```yaml
serial:
  port: /dev/ttyUSB0           # serial device path
  baudrate: 115200
  bytesize: 8                  # 5, 6, 7, or 8
  parity: "N"                  # N, E, O, M, S
  stopbits: 1                  # 1, 1.5, or 2
  timeout: 1.0                 # read timeout (seconds)
  write_timeout: 1.0           # write timeout (seconds)
  reconnect_delay: 2.0         # initial reconnect wait (seconds)
  max_reconnect_delay: 30.0    # cap for exponential backoff
  reconnect_backoff_multiplier: 1.5

buffer:
  max_size_mb: 10              # max buffer memory
  line_limit: 100000           # max buffered lines (FIFO overflow)
  overflow: fifo               # overflow strategy

server:
  name: ttygeist               # MCP server name
  transport: stdio             # "http" or "stdio"
  host: 127.0.0.1              # bind address (http only)
  port: 8443                   # bind port (http only)
  tls_cert: cert.pem           # TLS certificate path (http only)
  tls_key: key.pem             # TLS key path (http only)

socket:
  path: "~/tmp/ttygeist-{pid}.sock"  # Unix socket for CLI; {pid} is expanded

logging:
  file: ttygeist.log
  level: INFO                  # DEBUG, INFO, WARNING, ERROR, CRITICAL
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  max_size_mb: 50              # 0 = no rotation
  backup_count: 3
  request_log: false           # per-request DEBUG logging

auth:
  api_keys: []                 # list of valid API keys
  header_name: X-API-Key       # or "Authorization" for Bearer format
  allow_anon: false            # bypass auth (local dev only)
```

Only include keys you want to override -- missing keys fall back to the defaults shown above.

### Environment Variable Overrides

| Variable | Overrides | Notes |
|---|---|---|
| `TTYGEIST_PORT` | `serial.port` | |
| `TTYGEIST_API_KEYS` | `auth.api_keys` | Comma-separated; appended to file keys |
| `TTYGEIST_AUTH_HEADER` | `auth.header_name` | `X-API-Key` or `Authorization` |
| `TTYGEIST_ALLOW_ANON` | `auth.allow_anon` | `1`, `true`, or `yes` to enable |
| `TTYGEIST_REQUEST_LOG` | `logging.request_log` | `1`, `true`, or `yes` to enable |
| `TTYGEIST_TRANSPORT` | `server.transport` | `http` or `stdio` |

## Authentication

By default all HTTP access requires an API key.

Configure one or more keys in config.yaml under auth.api_keys or via `TTYGEIST_API_KEYS` (comma separated).

Two header styles are supported:
1. `X-API-Key: <key>`
2. `Authorization: Bearer <key>` (set auth.header_name to "Authorization" or export `TTYGEIST_AUTH_HEADER=Authorization`)

Example (custom header):
```bash
curl -s -k \
  -H 'X-API-Key: your-api-key-1' \
  https://127.0.0.1:8443/
```

Example (Bearer):
```bash
curl -s -k \
  -H 'Authorization: Bearer your-api-key-1' \
  https://127.0.0.1:8443/
```

Anonymous access
----------------
Set allow_anon: true (or export `TTYGEIST_ALLOW_ANON=1`) to run without auth middleware for local development. Do NOT enable in any untrusted environment.


## CLI Usage (ttygeist-cli)

Commands operate via the Unix socket and share the YAML config for socket path resolution.

### Show buffer

```bash
$ ttygeist-cli show -n 50
```

### Tail serial output

```bash
$ ttygeist-cli tail -n 20
```

### Show connection / buffer status

```bash
$ ttygeist-cli status
```

### Interactive terminal

```bash
$ ttygeist-cli terminal -n 10
```

The terminal is shared between the operator and the agent(s). Raw output appears immediately, although keystrokes are batched to enable coordinating. Control bytes (except for CR/LF) are flushed immediately, which should allow text user interface interactions.

Terminal can be exited with `Ctrl+]`, which restores terminal, and does not interfere with agent access.

## MCP Transport Modes

Two transport modes are supported:

1. STDIO (default)
   - Launches MCP over process stdin/stdout (suitable for desktop MCP clients like Claude Desktop).
   - Start: `uv run ttygeist`
   - Ignores HTTP-specific settings (host, port, tls_*). Auth headers are not used.
   - API keys / anonymous access are not relevant; the client already controls the local process.

2. HTTP(S)
   - Configure TLS cert/key in config.yaml (server.tls_cert / server.tls_key)
   - Requires API key unless allow_anon: true.
   - Start: `uv run ttygeist -- --transport http` or set `TTYGEIST_TRANSPORT=http`.

## MCP Tool Reference

### serial_read

* Read buffered lines by count or time slice.
* Parameters: lines (int), duration_seconds (float), clear_after_read (bool).

### serial_write

* Write UTF-8 data (may include control characters) to device.
* Parameters: data (string), add_newline (bool; appends CRLF if true and not already present).

### serial_status

* Connection metadata and buffer statistics snapshot.

### buffer_inspect

* Non-destructive tail view of recent lines (tail_lines parameter).

### buffer_clear

* Removes all currently buffered lines; returns count cleared.

### serial_reconnect

* Forces disconnect + reconnect attempt with fresh timing.

## License

Licensed under the MIT License. See the [LICENSE](https://github.com/ANXS/python/blob/master/LICENSE) file for details.

## Feedback, bug-reports, requests, ...

Are [welcome](https://github.com/otakup0pe/ttygeist/issues)!

## Author

The `ttygeist` tool was created by [Jonathan Freedman](https://jonathanfreedman.bio/) to facilitate testing just how many ESP32 devices he seems to be constantly surrounded by. This tool was created with LLM assistance.
