# ttygeist

![Maintenance](https://img.shields.io/maintenance/yes/2025.svg)

## Overview

ttygeist manages a single serial connection allowing both human and llm access via mcp. the server is accessible via http(s) or stdio. the human component has minimal interference and basic ansi code support. serial output will be (line) buffered even if a human or llm is not directly interacting with the serial device.

## Installation

The use of [uv](https://docs.astral.sh/uv/) is recommended because it is the bees knees.

```bash
$ cd ~/src/otakup0pe-ttygeist
$ uv sync
$ uv run ttygeist            # HTTP(S) mode (default)
$ uv run ttygeist -- --transport stdio   # STDIO mode
```

Environment override alternative:
```bash
$ export TTYGEIST_TRANSPORT=stdio
$ uv run ttygeist
```

## Configuration (High Level)

Please see `config.example.yaml` for full suite configuration options.

* Device configuration for port, baud, parity, etc
* Serial buffer size, line limit, etc
* Server configuration for stdio or http(s)
* Local socket configuration for cli

### Environment Variable Overrides

Currently supported overrides (from config.py):
- `TTYGEIST_API_KEYS`: comma‑separated list of API keys appended to auth.api_keys.
- `TTYGEIST_PORT`: overrides serial.port.
- `TTYGEIST_AUTH_HEADER`: override auth.header_name ("X-API-Key" or "Authorization").
- `TTYGEIST_ALLOW_ANON`: set to 1/true/yes to disable auth middleware (NOT for production).
- `TTYGEIST_REQUEST_LOG`: set to 1/true/yes to enable per-request DEBUG logging.
- `TTYGEIST_TRANSPORT`: set to "http" or "stdio" to override server.transport.

(Additional overrides can be added; none other are presently implemented.)

## Authentication

By default all HTTP access requires an API key.

Configure one or more keys in config.yaml under auth.api_keys or via TTYGEIST_API_KEYS (comma separated).

Two header styles are supported:
1. X-API-Key: <key>
2. Authorization: Bearer <key> (set auth.header_name to "Authorization" or export TTYGEIST_AUTH_HEADER=Authorization)

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
Set allow_anon: true (or export TTYGEIST_ALLOW_ANON=1) to run without auth middleware for local development. Do NOT enable in any untrusted environment.


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

Behavior in terminal mode:
* Raw output appears immediately (prompts, partial lines, progress updates).
* Keystrokes are batched (~20ms) for throughput; control bytes (except CR/LF) flush immediately.
* Only Ctrl+] exits.
* On exit, terminal settings are restored and both socket clients close cleanly.

If the socket cannot be found you’ll get an error suggesting you start the MCP server first.

## MCP Transport Modes

Two transport modes are supported:

1. HTTP(S) (default)
   - Configure TLS cert/key in config.yaml (server.tls_cert / server.tls_key)
   - Requires API key unless allow_anon: true.
   - Start: `uv run ttygeist`

2. STDIO
   - Launches MCP over process stdin/stdout (suitable for desktop MCP clients like Claude Desktop).
   - Start: `uv run ttygeist -- --transport stdio` or set `TTYGEIST_TRANSPORT=stdio`.
   - Ignores HTTP-specific settings (host, port, tls_*). Auth headers are not used.
   - API keys / anonymous access are not relevant; the client already controls the local process.

## MCP Tool Reference

### serial_read

* Read buffered lines by count or time slice.
* Parameters: lines (int), duration_seconds (float), clear_after_read (bool).

### serial_write

* Write UTF‑8 data (may include control characters) to device.
* Parameters: data (string), add_newline (bool; appends CRLF if true and not already present).

### serial_status

* Connection metadata and buffer statistics snapshot.

### buffer_inspect

* Non‑destructive tail view of recent lines (tail_lines parameter).

### buffer_clear

* Removes all currently buffered lines; returns count cleared.

### serial_reconnect

* Forces disconnect + reconnect attempt with fresh timing.

## License

Licensed under the MIT License. See the [LICENSE](https://github.com/ANXS/python/blob/master/LICENSE) file for details.

## Feedback, bug-reports, requests, ...

Are [welcome](https://github.com/otakup0pe/ttygeist/issues)!
