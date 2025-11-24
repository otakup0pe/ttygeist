"""Unix socket server for IPC with CLI clients (line + raw streaming)."""

import json
import logging
import os
import socket
import select
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SocketServer:
    """Unix socket server for local IPC.

    Supports two streaming modes:
    - buffer_stream: line-oriented updates via the line buffer
    - raw_stream: raw byte chunks as they arrive (for interactive terminal)
    """

    def __init__(self, socket_path: str, buffer_manager, serial_manager):
        self.socket_path = socket_path
        self.buffer_manager = buffer_manager
        self.serial_manager = serial_manager
        self.server_socket: Optional[socket.socket] = None
        self.running = False
        self.server_thread: Optional[threading.Thread] = None

    # ---------------------- Lifecycle ----------------------
    def start(self):
        if self.running:
            logger.warning("Socket server already running")
            return
        socket_dir = Path(self.socket_path).parent
        socket_dir.mkdir(parents=True, exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        self.server_socket.listen(5)
        os.chmod(self.socket_path, 0o600)
        self.running = True
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        logger.info(f"Socket server started on {self.socket_path}")

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except Exception:
                pass
        logger.info("Socket server stopped")

    # ---------------------- Accept loop ----------------------
    def _run_server(self):
        while self.running:
            try:
                client_socket, _ = self.server_socket.accept()
                t = threading.Thread(target=self._handle_client, args=(client_socket,), daemon=True)
                t.start()
            except Exception as e:
                if self.running:
                    logger.error(f"Error accepting connection: {e}")

    # ---------------------- Client handler ----------------------
    def _handle_client(self, client_socket: socket.socket):
        buffer = b""
        try:
            while True:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line:
                        continue
                    try:
                        request = json.loads(line.decode('utf-8'))
                    except json.JSONDecodeError as e:
                        self._send(client_socket, {"result": None, "error": f"Invalid JSON: {e}", "id": None})
                        continue
                    response = self._handle_request(request, client_socket)
                    if response is not None:
                        self._send(client_socket, response)
        except Exception as e:
            logger.error(f"Error in client handler: {e}")
        finally:
            try:
                client_socket.close()
            except Exception:
                pass

    def _send(self, client_socket: socket.socket, response: Dict[str, Any]):
        try:
            client_socket.sendall(json.dumps(response).encode('utf-8') + b"\n")
        except Exception as e:
            logger.error(f"Failed sending response: {e}")

    # ---------------------- Request dispatch ----------------------
    def _handle_request(self, request: Dict[str, Any], client_socket: socket.socket) -> Optional[Dict[str, Any]]:
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")
        try:
            if method == "buffer_inspect":
                tail_lines = params.get("tail_lines", 32)
                lines = self.buffer_manager.read_tail(tail_lines)
                result = {"lines": [l.data + "\n" for l in lines], "count": len(lines)}
                return {"result": result, "error": None, "id": req_id}
            if method == "buffer_stream":
                self._stream_buffer(client_socket, req_id)
                return None
            if method == "raw_stream":
                self._stream_raw(client_socket, req_id)
                return None
            if method == "serial_status":
                status = self.serial_manager.get_status()
                buffer_stats = self.buffer_manager.get_stats()
                result = {
                    "connected": status["connected"],
                    "port": status["port"],
                    "baudrate": status["baudrate"],
                    "uptime_seconds": status["uptime_seconds"],
                    "error_count": status["error_count"],
                    "reconnect_count": status["reconnect_count"],
                    "last_error": status["last_error"],
                    "buffer_stats": buffer_stats,
                }
                return {"result": result, "error": None, "id": req_id}
            if method == "serial_write":
                data = params.get("data", "")
                add_nl = params.get("add_newline", True)
                result = self.serial_manager.write(data, add_nl)
                return {"result": result, "error": None, "id": req_id}
            return {"result": None, "error": f"Unknown method: {method}", "id": req_id}
        except Exception as e:
            logger.error(f"Error executing {method}: {e}")
            return {"result": None, "error": str(e), "id": req_id}

    # ---------------------- Streaming helpers ----------------------
    def _stream_buffer(self, client_socket: socket.socket, req_id: Any):
        try:
            last_count = self.buffer_manager.get_stats()["current_lines"]
            client_socket.setblocking(False)
            while True:
                # Detect client disconnect without blocking writes forever
                try:
                    r, _, _ = select.select([client_socket], [], [], 0.1)
                    if r:
                        probe = client_socket.recv(1, socket.MSG_PEEK)
                        if not probe:
                            break  # client hung up
                except Exception:
                    break
                current_count = self.buffer_manager.get_stats()["current_lines"]
                if current_count > last_count:
                    new_lines = current_count - last_count
                    all_lines = self.buffer_manager.read_tail(current_count)
                    lines_to_send = all_lines[-new_lines:]
                    for line in lines_to_send:
                        msg = {"stream": line.data + "\n", "id": req_id}
                        self._send(client_socket, msg)
                    last_count = current_count
        except Exception as e:
            logger.debug(f"Line stream ended: {e}")

    def _stream_raw(self, client_socket: socket.socket, req_id: Any):
        import time
        import queue
        listener_q = self.serial_manager.add_raw_listener()
        try:
            while True:
                try:
                    chunk = listener_q.get(timeout=0.5)
                except queue.Empty:
                    # heartbeat message? skip for now
                    continue
                # Send raw chunk as base64 to avoid binary framing issues
                import base64
                b64 = base64.b64encode(chunk).decode('ascii')
                msg = {"raw": b64, "id": req_id}
                self._send(client_socket, msg)
        except Exception as e:
            logger.debug(f"Raw stream ended: {e}")
        finally:
            self.serial_manager.remove_raw_listener(listener_q)
