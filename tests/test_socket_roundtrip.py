"""Integration tests for SocketServer + SocketClient over real Unix sockets."""

import os
import time

import pytest

from ttygeist.buffer_manager import BufferManager
from ttygeist.socket_client import SocketClient
from ttygeist.socket_server import SocketServer


class FakeSerialManager:
    """Minimal stand-in for SerialManager in socket tests."""

    def __init__(self):
        self.is_connected = True
        self.last_write = None

    def get_status(self):
        return {
            "connected": self.is_connected,
            "port": "/dev/ttyTEST",
            "baudrate": 115200,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "uptime_seconds": 42.0,
            "error_count": 0,
            "reconnect_count": 1,
            "last_error": None,
        }

    def write(self, data, add_newline=True):
        self.last_write = data
        return {"success": True, "bytes_written": len(data), "data_sent": data}


@pytest.fixture()
def socket_pair(tmp_path):
    """Start a SocketServer and yield a connected SocketClient, then clean up."""
    sock_path = str(tmp_path / "test.sock")
    buf = BufferManager(max_size_bytes=4096, line_limit=100)
    serial_mgr = FakeSerialManager()
    server = SocketServer(sock_path, buf, serial_mgr)
    server.start()

    # Give the server thread a moment to bind
    time.sleep(0.15)

    client = SocketClient(sock_path)
    client.connect()

    yield client, server, buf, serial_mgr

    client.close()
    server.stop()


@pytest.mark.timeout(5)
class TestBufferInspect:
    def test_empty_buffer(self, socket_pair):
        client, _, buf, _ = socket_pair
        result = client.buffer_inspect(tail_lines=10)
        assert result["count"] == 0
        assert result["lines"] == []

    def test_with_data(self, socket_pair):
        client, _, buf, _ = socket_pair
        buf.append("line1")
        buf.append("line2")
        buf.append("line3")
        result = client.buffer_inspect(tail_lines=2)
        assert result["count"] == 2
        assert "line2\n" in result["lines"]
        assert "line3\n" in result["lines"]


@pytest.mark.timeout(5)
class TestSerialStatus:
    def test_returns_status(self, socket_pair):
        client, _, _, _ = socket_pair
        result = client.serial_status()
        assert result["connected"] is True
        assert result["port"] == "/dev/ttyTEST"
        assert result["baudrate"] == 115200
        assert "buffer_stats" in result


@pytest.mark.timeout(5)
class TestSerialWrite:
    def test_write_round_trip(self, socket_pair):
        client, _, _, serial_mgr = socket_pair
        result = client.serial_write("test command")
        assert result["success"] is True
        assert serial_mgr.last_write == "test command"

    def test_write_no_newline(self, socket_pair):
        client, _, _, serial_mgr = socket_pair
        result = client.serial_write("raw", add_newline=False)
        assert result["success"] is True


@pytest.mark.timeout(5)
class TestUnknownMethod:
    def test_unknown_method_errors(self, socket_pair):
        client, _, _, _ = socket_pair
        with pytest.raises(RuntimeError, match="Unknown method"):
            client._send_request("nonexistent_method")


@pytest.mark.timeout(5)
class TestServerLifecycle:
    def test_stop_removes_socket(self, tmp_path):
        sock_path = str(tmp_path / "lifecycle.sock")
        buf = BufferManager(max_size_bytes=1024, line_limit=10)
        serial_mgr = FakeSerialManager()
        server = SocketServer(sock_path, buf, serial_mgr)
        server.start()
        time.sleep(0.1)
        assert os.path.exists(sock_path)
        server.stop()
        assert not os.path.exists(sock_path)
