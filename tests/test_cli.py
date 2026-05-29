"""Tests for the CLI interface."""

import time

import pytest

from ttygeist.buffer_manager import BufferManager
from ttygeist.cli import cmd_show, cmd_status, find_socket_path
from ttygeist.socket_server import SocketServer


class FakeSerialManager:
    """Minimal stand-in for SerialManager in CLI tests."""

    def __init__(self):
        self.is_connected = True

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
        return {"success": True, "bytes_written": len(data), "data_sent": data}


class FakeDeviceEntry:
    """Minimal stand-in for DeviceEntry."""

    def __init__(self, name, buffer_manager, serial_manager):
        self.name = name
        self.buffer_manager = buffer_manager
        self.serial_manager = serial_manager

    @property
    def connected(self):
        return self.serial_manager.is_connected


class FakeDeviceRegistry:
    """Minimal stand-in for DeviceRegistry that returns a single device."""

    def __init__(self, buffer_manager, serial_manager):
        self._entry = FakeDeviceEntry("test", buffer_manager, serial_manager)

    def resolve(self, name=None):
        return self._entry


def _socket_config(path):
    """Return a YAML config string pointing at a specific socket path."""
    return f"socket:\n  path: '{path}'\n"


@pytest.fixture()
def cli_server(tmp_path):
    """Start a SocketServer with a known socket path and yield components."""
    sock_path = str(tmp_path / "ttygeist-99999.sock")
    buf = BufferManager(max_size_bytes=4096, line_limit=100)
    serial_mgr = FakeSerialManager()
    registry = FakeDeviceRegistry(buf, serial_mgr)
    server = SocketServer(sock_path, registry)
    server.start()
    time.sleep(0.15)
    yield sock_path, server, buf, serial_mgr
    server.stop()


class FakeArgs:
    """Minimal argparse namespace substitute."""

    def __init__(self, **kwargs):
        self.config = None
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---- find_socket_path ----


@pytest.mark.timeout(5)
class TestFindSocketPath:
    def test_finds_socket_by_glob(self, tmp_path, tmp_config_file):
        """find_socket_path globs for {pid} sockets and finds the right one."""
        sock_path = str(tmp_path / "ttygeist-12345.sock")
        with open(sock_path, "w"):
            pass

        template = str(tmp_path / "ttygeist-{pid}.sock")
        config_path = tmp_config_file(_socket_config(template))

        result = find_socket_path(config_path)
        assert result == sock_path

    def test_no_socket_exits(self, tmp_path, tmp_config_file):
        """find_socket_path exits when no socket file exists."""
        template = str(tmp_path / "ttygeist-{pid}.sock")
        config_path = tmp_config_file(_socket_config(template))

        with pytest.raises(SystemExit):
            find_socket_path(config_path)

    def test_multiple_sockets_exits(self, tmp_path, tmp_config_file):
        """find_socket_path exits when multiple sockets match."""
        for pid in (11111, 22222):
            with open(str(tmp_path / f"ttygeist-{pid}.sock"), "w"):
                pass

        template = str(tmp_path / "ttygeist-{pid}.sock")
        config_path = tmp_config_file(_socket_config(template))

        with pytest.raises(SystemExit):
            find_socket_path(config_path)

    def test_no_pid_template(self, tmp_config_file):
        """find_socket_path returns path directly when no {pid} in template."""
        config_path = tmp_config_file(_socket_config("/tmp/ttygeist-fixed.sock"))

        result = find_socket_path(config_path)
        assert result == "/tmp/ttygeist-fixed.sock"

    def test_missing_config_uses_defaults(self, tmp_path, tmp_config_file):
        """find_socket_path with no matching socket and a tmp-dir template exits."""
        # Point at a tmp_path so we don't accidentally find a real socket in ~/tmp
        template = str(tmp_path / "ttygeist-{pid}.sock")
        config_path = tmp_config_file(_socket_config(template))

        with pytest.raises(SystemExit):
            find_socket_path(config_path)


# ---- cmd_status ----


@pytest.mark.timeout(5)
class TestCmdStatus:
    def test_prints_status(self, cli_server, tmp_config_file, capsys):
        """cmd_status prints connection and buffer info."""
        sock_path, _, buf, _ = cli_server
        config_path = tmp_config_file(_socket_config(sock_path))

        args = FakeArgs(config=config_path)
        cmd_status(args)

        captured = capsys.readouterr()
        assert "Connected: True" in captured.out
        assert "Port: /dev/ttyTEST" in captured.out
        assert "Baudrate: 115200" in captured.out
        assert "Buffer Status:" in captured.out
        assert "Current lines:" in captured.out

    def test_status_disconnected(self, cli_server, tmp_config_file, capsys):
        """cmd_status reflects disconnected state."""
        sock_path, _, _, serial_mgr = cli_server
        serial_mgr.is_connected = False
        config_path = tmp_config_file(_socket_config(sock_path))

        args = FakeArgs(config=config_path)
        cmd_status(args)

        captured = capsys.readouterr()
        assert "Connected: False" in captured.out


# ---- cmd_show ----


@pytest.mark.timeout(5)
class TestCmdShow:
    def test_show_empty(self, cli_server, tmp_config_file, capsys):
        """cmd_show with empty buffer prints nothing."""
        sock_path, _, _, _ = cli_server
        config_path = tmp_config_file(_socket_config(sock_path))

        args = FakeArgs(config=config_path, lines=10)
        cmd_show(args)

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_show_with_data(self, cli_server, tmp_config_file, capsys):
        """cmd_show prints buffered lines."""
        sock_path, _, buf, _ = cli_server
        buf.append("hello world")
        buf.append("second line")
        config_path = tmp_config_file(_socket_config(sock_path))

        args = FakeArgs(config=config_path, lines=10)
        cmd_show(args)

        captured = capsys.readouterr()
        assert "hello world" in captured.out
        assert "second line" in captured.out

    def test_show_respects_line_count(self, cli_server, tmp_config_file, capsys):
        """cmd_show -n limits output."""
        sock_path, _, buf, _ = cli_server
        for i in range(10):
            buf.append(f"line-{i}")
        config_path = tmp_config_file(_socket_config(sock_path))

        args = FakeArgs(config=config_path, lines=3)
        cmd_show(args)

        captured = capsys.readouterr()
        assert "line-7" in captured.out
        assert "line-8" in captured.out
        assert "line-9" in captured.out
        assert "line-0" not in captured.out
