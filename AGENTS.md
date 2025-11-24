## Project Overview

The ttygeist project provides a MCP that enables LLM interactions
with a single serial port. Humans may also make use of the port a the
same time. Look at the `README.md` for more information on the project.

## Architecture

High-level components:

* `serial_manager.py`: owns pyserial connection; emits raw chunks; assembles lines; auto reconnect logic.
* `buffer_manager.py`: fixed-size FIFO line storage with stats and overflow handling.
* `socket_server.py`: Unix domain socket RPC + two streaming modes (line, raw).
* `socket_client.py`: client abstraction (separate instances recommended for streaming vs requests).
* `cli.py`: user-facing CLI commands + interactive terminal batching and exit handling.
* `terminal_emulator.py`: lightweight parser for ANSI/VT100 sequences, CR/LF logic.
* `config.py`: loads YAML, applies env var overrides, exposes convenience properties.

Data flow (simplified):

LLM (MCP HTTPS) → MCP tool handlers → buffer_manager / serial_manager
CLI (Unix socket) → socket_server → serial_manager (writes) & buffer_manager/raw listeners
Serial device ↔ serial_manager → raw listeners & line buffer
