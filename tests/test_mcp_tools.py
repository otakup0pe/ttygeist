"""Tests for MCP tool functions defined in server.py.

These tests exercise the actual async tool wrappers registered on the
FastMCP instance, with a real BufferManager and a mocked SerialManager.
No HTTP server is started. Uses a single-device config so the device
parameter is implicit.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client

from ttygeist.buffer_manager import BufferManager
from ttygeist.config import Config, DeviceConfig


def _serial_mock():
    """Create a mock SerialManager with standard return values."""
    mock = MagicMock()
    mock.is_connected = True
    mock.get_status.return_value = {
        "connected": True,
        "port": "/dev/ttyTEST",
        "baudrate": 115200,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "uptime_seconds": 10.0,
        "error_count": 0,
        "reconnect_count": 0,
        "last_error": None,
    }
    mock.write.return_value = {
        "success": True,
        "bytes_written": 7,
        "data_sent": "cmd\r\n",
    }
    mock.reconnect.return_value = {
        "success": True,
        "connected": True,
        "error": None,
    }
    mock.start.return_value = None
    mock.stop.return_value = None
    return mock


async def _call(client, tool_name, **kwargs):
    """Call a tool via the public FastMCP Client API and return the result dict."""
    result = await client.call_tool(tool_name, kwargs or {})
    if result.data is not None:
        return result.data
    for block in result.content:
        if hasattr(block, "text"):
            return json.loads(block.text)
    raise ValueError(f"No parseable result from {tool_name}")


@pytest.fixture()
def mcp_env():
    """Create a FastMCP server with a single-device registry.

    Uses a real BufferManager and mocked SerialManager. Single device
    named 'testdev' so the device parameter is implicit in all tools.
    """
    import ttygeist.server as srv

    serial_mock = _serial_mock()
    buf = BufferManager(max_size_bytes=4096, line_limit=100)

    # Build a minimal config with one device
    cfg = Config(
        devices={"testdev": DeviceConfig(name="testdev", port="/dev/ttyTEST")},
        server_name="test",
    )

    with (
        patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[]),
        patch("ttygeist.serial_manager.SerialManager", return_value=serial_mock),
        patch.object(srv, "SocketServer") as mock_ss_cls,
        patch("signal.signal"),
        patch("atexit.register"),
    ):
        mock_ss_cls.return_value = MagicMock()

        mcp = srv.create_mcp_server(cfg)

        # Inject our buffer and mock into the registry entry
        entry = srv.registry.devices["testdev"]
        entry.buffer_manager = buf
        entry.serial_manager = serial_mock

        client = Client(mcp)
        yield client, buf, serial_mock

    # Reset module globals
    srv.registry = None
    srv.socket_server = None
    srv._shutdown_executed = False


class TestSerialRead:
    async def test_read_all(self, mcp_env):
        client, buf, _ = mcp_env
        buf.append("line1")
        buf.append("line2")
        async with client:
            result = await _call(client, "serial_read")
        assert result["success"] is True
        assert result["lines_read"] == 2
        assert result["data"][0]["data"] == "line1"
        assert result["device"] == "testdev"

    async def test_read_with_limit(self, mcp_env):
        client, buf, _ = mcp_env
        for i in range(10):
            buf.append(f"line{i}")
        async with client:
            result = await _call(client, "serial_read", lines=3)
        assert result["success"] is True
        assert result["lines_read"] == 3
        assert result["data"][0]["data"] == "line7"

    async def test_read_clear_after(self, mcp_env):
        client, buf, _ = mcp_env
        buf.append("gone")
        async with client:
            result = await _call(client, "serial_read", clear_after_read=True)
        assert result["success"] is True
        assert result["lines_read"] == 1
        assert buf.read() == []


class TestBufferClear:
    async def test_clear(self, mcp_env):
        client, buf, _ = mcp_env
        buf.append("data1")
        buf.append("data2")
        async with client:
            result = await _call(client, "buffer_clear")
        assert result["success"] is True
        assert result["lines_cleared"] == 2
        assert result["device"] == "testdev"


class TestBufferInspect:
    async def test_inspect(self, mcp_env):
        client, buf, _ = mcp_env
        for i in range(5):
            buf.append(f"line{i}")
        async with client:
            result = await _call(client, "buffer_inspect", tail_lines=3)
        assert result["success"] is True
        assert result["tail_lines"] == 3
        assert result["data"][0]["data"] == "line2"
        assert result["statistics"]["current_lines"] == 5
        assert result["device"] == "testdev"


class TestSerialWrite:
    async def test_write_delegates_to_manager(self, mcp_env):
        client, _, serial_mock = mcp_env
        async with client:
            result = await _call(client, "serial_write", data="cmd", add_newline=True)
        assert result["success"] is True
        assert result["bytes_written"] == 7
        assert result["device"] == "testdev"
        serial_mock.write.assert_called_once_with(data="cmd", add_newline=True)

    async def test_write_decodes_hex_escape(self, mcp_env):
        client, _, serial_mock = mcp_env
        async with client:
            await _call(client, "serial_write", data="\\x03", add_newline=False)
        serial_mock.write.assert_called_once_with(data="\x03", add_newline=False)

    async def test_write_decodes_newline_escape(self, mcp_env):
        client, _, serial_mock = mcp_env
        async with client:
            await _call(client, "serial_write", data="hello\\n", add_newline=False)
        serial_mock.write.assert_called_once_with(data="hello\n", add_newline=False)

    async def test_write_decodes_unicode_escape(self, mcp_env):
        client, _, serial_mock = mcp_env
        async with client:
            await _call(client, "serial_write", data="\\u0041", add_newline=False)
        serial_mock.write.assert_called_once_with(data="A", add_newline=False)

    async def test_write_plain_text_unchanged(self, mcp_env):
        client, _, serial_mock = mcp_env
        async with client:
            await _call(client, "serial_write", data="hello world", add_newline=False)
        serial_mock.write.assert_called_once_with(data="hello world", add_newline=False)


class TestSerialControl:
    async def test_reconnect(self, mcp_env):
        client, _, serial_mock = mcp_env
        async with client:
            result = await _call(client, "serial_control", action="reconnect")
        assert result["success"] is True
        assert result["action"] == "reconnect"
        assert result["device"] == "testdev"
        serial_mock.reconnect.assert_called_once()

    async def test_suspend(self, mcp_env):
        client, _, serial_mock = mcp_env
        serial_mock.suspend.return_value = {
            "success": True,
            "already_suspended": False,
            "connected": False,
        }
        async with client:
            result = await _call(client, "serial_control", action="suspend")
        assert result["success"] is True
        assert result["action"] == "suspend"
        serial_mock.suspend.assert_called_once()

    async def test_resume(self, mcp_env):
        client, _, serial_mock = mcp_env
        serial_mock.resume.return_value = {
            "success": True,
            "connected": True,
            "error": None,
        }
        async with client:
            result = await _call(client, "serial_control", action="resume")
        assert result["success"] is True
        assert result["action"] == "resume"
        serial_mock.resume.assert_called_once()

    async def test_invalid_action(self, mcp_env):
        client, _, _ = mcp_env
        async with client:
            result = await _call(client, "serial_control", action="explode")
        assert result["success"] is False
        assert "Unknown action" in result["error"]


class TestSerialStatus:
    async def test_single_device_status(self, mcp_env):
        client, _, serial_mock = mcp_env
        async with client:
            result = await _call(client, "serial_status")
        assert result["success"] is True
        assert result["device"] == "testdev"
        assert result["serial"]["connected"] is True
        assert "current_lines" in result["buffer"]

    async def test_explicit_device_name(self, mcp_env):
        client, _, _ = mcp_env
        async with client:
            result = await _call(client, "serial_status", device="testdev")
        assert result["success"] is True
        assert result["device"] == "testdev"

    async def test_unknown_device(self, mcp_env):
        client, _, _ = mcp_env
        async with client:
            result = await _call(client, "serial_status", device="nosuchdevice")
        assert result["success"] is False
        assert "not found" in result["error"].lower()
