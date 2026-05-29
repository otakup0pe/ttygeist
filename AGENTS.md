# Agent Guidelines for ttygeist

## Project Overview

ttygeist is an MCP server for serial port communication. It discovers and
manages multiple serial devices concurrently, bridging LLMs (via MCP) and
human operators (via CLI/Unix socket). Written in Python 3.10+.

See `README.md` for user-facing documentation.

## Architecture

```
LLM (MCP STDIO/HTTP) -> MCP tool handlers -> device_registry -> per-device buffer/serial
CLI (Unix socket)     -> socket_server     -> serial_manager (writes) & buffer_manager
Serial device(s)     <-> serial_manager(s) -> raw listeners & line buffers
```

High-level components:

* `config.py`: YAML config parser. Device accept/block lists, chipset defaults,
  disabled_tools, firmware_patterns. Dataclass-based (no class methods).
* `device_registry.py`: `DeviceRegistry` manages named `DeviceEntry` instances,
  each with its own `SerialManager` + `BufferManager`. USB auto-discovery via
  `serial.tools.list_ports`. Background threads for USB hot-plug scanning and
  firmware boot log identification.
* `server.py`: FastMCP server, MCP tool definitions with `device` parameter,
  signal handling, journald/file logging, HTTP/STDIO transports.
* `serial_manager.py`: pyserial connection with auto-reconnect, threading,
  raw byte streaming, suspend/resume for external tool access.
* `buffer_manager.py`: Thread-safe circular buffer (deque) with FIFO overflow.
* `socket_server.py` / `socket_client.py`: Unix socket IPC for CLI.
* `cli.py`: Human CLI interface (show, tail, status, terminal).
* `auth.py`: ASGI middleware for API key auth (HTTP transport only).
* `terminal_emulator.py`: ANSI/VT100 escape sequence state machine.

## Key Design Decisions

* **Always discover.** ttygeist enumerates all serial ports on startup and
  periodically. Accept list (named devices) and block list mediate what gets
  managed. No "modes" to configure.
* **Device parameter.** All MCP tools accept an optional `device` name. When
  only one device exists, it is implicit. With multiple devices and no name,
  tools return an error listing available devices.
* **Chipset defaults.** VID/PID -> default serial settings (baud, DTR, RTS).
  Per-device config overrides chipset defaults.
* **Firmware patterns are config, not code.** Boot log identification patterns
  are supplied via `firmware_patterns:` in config, not hardcoded.
* **disabled_tools config.** Users can suppress tool registration for tools
  they don't need, reducing MCP context footprint.
* **Journald by default.** Uses `python-systemd` JournalHandler when available,
  falls back to file logging.

## Development

### Dependencies

Uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

### Running tests

```bash
make test
```

Runs `ruff check`, `ruff format --check`, then `pytest` with 10s timeout.

### Linting

```bash
make lint          # check only
make lint-fix      # auto-fix
```

Ruff rules: E, F, W, I, UP, B, SIM. E501 ignored. Target: Python 3.10.

### CI

GitHub Actions: lint + test across Python 3.10-3.13 on push/PR to `mainline`.

## Code Conventions

* No unicode in source files (ASCII hyphens only).
* `asyncio_mode = "auto"` in pytest.
* Mocking boundary: pyserial is mocked, buffer_manager is real.
* Socket tests use real Unix sockets in `tmp_path`.
* Config is dataclass-based with `load_config()` free function (not a class with methods).

## MCP Tools

Seven tools: `serial_status`, `serial_read`, `serial_write`, `buffer_inspect`,
`buffer_clear`, `serial_control`, `server_shutdown`. All accept optional
`device` parameter. See README.md for details.
