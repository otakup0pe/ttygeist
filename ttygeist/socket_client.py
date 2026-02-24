"""Unix socket client for CLI communication with MCP server (line + raw)."""

import base64
import json
import os
import select
import socket
from collections.abc import Callable
from typing import Any


class SocketClient:
    def __init__(self, socket_path: str):
        self.socket_path = os.path.expanduser(socket_path)
        self.sock: socket.socket | None = None
        self._request_id = 0

    def connect(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self.socket_path)
            return True
        except FileNotFoundError as e:
            raise ConnectionError(
                f"Socket not found at {self.socket_path}\nIs the MCP server running? Start it with: uv run serial-mcp"
            ) from e
        except ConnectionRefusedError as e:
            raise ConnectionError(
                f"Connection refused to {self.socket_path}\nIs the MCP server running? Start it with: uv run serial-mcp"
            ) from e
        except Exception as e:
            raise ConnectionError(f"Failed to connect to socket: {e}") from e

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.sock:
            raise RuntimeError("Not connected to server")
        request = {"method": method, "params": params or {}, "id": self._next_id()}
        self.sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buffer = b""
        while b"\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection")
            buffer += chunk
        line, _ = buffer.split(b"\n", 1)
        response = json.loads(line.decode("utf-8"))
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response.get("result")

    # ---------------------- Standard methods ----------------------
    def buffer_inspect(self, tail_lines: int = 32) -> dict[str, Any]:
        return self._send_request("buffer_inspect", {"tail_lines": tail_lines})

    def serial_status(self) -> dict[str, Any]:
        return self._send_request("serial_status")

    def serial_write(self, data: str, add_newline: bool = True) -> dict[str, Any]:
        return self._send_request("serial_write", {"data": data, "add_newline": add_newline})

    # ---------------------- Streaming (line) ----------------------
    def buffer_stream(self, callback: Callable[[str], None], stop_event: Any | None = None, poll_interval: float = 0.1):
        if not self.sock:
            raise RuntimeError("Not connected to server")
        request = {"method": "buffer_stream", "params": {}, "id": self._next_id()}
        self.sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buffer = b""
        try:
            while True:
                if stop_event and stop_event.is_set():
                    break
                ready, _, _ = select.select([self.sock], [], [], poll_interval)
                if not ready:
                    continue
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    if "stream" in msg:
                        callback(msg["stream"])
                    elif "error" in msg and msg["error"]:
                        raise RuntimeError(msg["error"])
        except KeyboardInterrupt:
            pass

    # ---------------------- Streaming (raw) ----------------------
    def raw_stream(self, callback: Callable[[bytes], None], stop_event: Any | None = None, poll_interval: float = 0.05):
        if not self.sock:
            raise RuntimeError("Not connected to server")
        request = {"method": "raw_stream", "params": {}, "id": self._next_id()}
        self.sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buffer = b""
        try:
            while True:
                if stop_event and stop_event.is_set():
                    break
                ready, _, _ = select.select([self.sock], [], [], poll_interval)
                if not ready:
                    continue
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    if "raw" in msg:
                        try:
                            data = base64.b64decode(msg["raw"], validate=True)
                        except Exception:
                            continue
                        callback(data)
                    elif "error" in msg and msg["error"]:
                        raise RuntimeError(msg["error"])
        except KeyboardInterrupt:
            pass
