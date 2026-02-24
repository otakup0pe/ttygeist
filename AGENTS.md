# Agent Guidelines for ttygeist

## Project Overview

ttygeist is a Model Context Protocol (MCP) server for serial port communication.
It bridges LLMs (via MCP) and human operators (via CLI/Unix socket) to interact
with a single serial device concurrently. Written in Python 3.10+.

See `README.md` for user-facing documentation.

## Architecture

High-level components:

* `server.py`: FastMCP server, MCP tool definitions, signal handling, HTTP/STDIO transports.
* `serial_manager.py`: pyserial connection with auto-reconnect, threading, raw byte streaming.
* `buffer_manager.py`: thread-safe circular buffer (deque) with FIFO overflow.
* `socket_server.py` / `socket_client.py`: Unix socket IPC for CLI communication.
* `cli.py`: human CLI interface (show, tail, status, terminal commands).
* `auth.py`: ASGI middleware for API key auth (constant-time comparison, HTTP transport only).
* `terminal_emulator.py`: ANSI/VT100 escape sequence state machine for display cleanup.
* `config.py`: YAML config + env var overrides, sensible defaults for all keys.

Data flow:

```
LLM (MCP STDIO/HTTP) -> MCP tool handlers -> buffer_manager / serial_manager
CLI (Unix socket)     -> socket_server     -> serial_manager (writes) & buffer_manager
Serial device        <-> serial_manager    -> raw listeners & line buffer
```

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

This runs `ruff check`, `ruff format --check`, then `pytest` with a 10 second timeout.
Lint must pass before tests run.

### Linting

```bash
make lint          # check only
make lint-fix      # auto-fix
```

Ruff rules: E, F, W, I, UP, B, SIM. Line length (E501) is ignored.
Target version is Python 3.10.

### CI

GitHub Actions runs lint + test across Python 3.10-3.13 on every push/PR to `mainline`.

## Configuration

All config keys have sensible defaults -- no config file is required. The default
transport is STDIO. See the Configuration section in `README.md` for the full
reference of all keys, defaults, and environment variable overrides.

## Code Conventions

* No unicode characters in source files (use ASCII hyphens, not non-breaking hyphens).
* `asyncio_mode = "auto"` in pytest -- async tests do not need explicit markers.
* Mocking boundary: pyserial is mocked in tests, buffer_manager is used for real.
* Socket tests use real Unix sockets in pytest `tmp_path`.
* Tests use pytest fixtures and `@pytest.mark.parametrize` to reduce duplication.

## MCP Tools

The server exposes six tools: `serial_read`, `serial_write`, `serial_status`,
`buffer_inspect`, `buffer_clear`, `serial_reconnect`. See the MCP Tool Reference
section in `README.md` for parameter details.
