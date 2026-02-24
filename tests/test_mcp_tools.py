"""Tests for MCP tool functions defined in server.py.

These tests exercise the actual async tool wrappers registered on the
FastMCP instance, with a real BufferManager and a mocked SerialManager.
No HTTP server is started.
"""

from unittest.mock import MagicMock, patch

import pytest

from ttygeist.buffer_manager import BufferManager


def _serial_mock():
    """Create a mock SerialManager with standard return values."""
    mock = MagicMock()
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
    return mock


def _get_tool(mcp, name: str):
    """Retrieve a registered tool function by name from the FastMCP instance."""
    # FastMCP 3.x stores tools in _local_provider._components keyed as "tool:<name>@[version]"
    # Unversioned tools use a trailing @ with no version string
    components = mcp._local_provider._components
    tool = components.get(f"tool:{name}@") or components.get(f"tool:{name}")
    if tool is None:
        available = [k for k in mcp._local_provider._components if k.startswith("tool:")]
        raise KeyError(f"Tool {name!r} not registered. Available: {available}")
    return tool.fn


@pytest.fixture()
def mcp_env():
    """Create a FastMCP server with real BufferManager and mocked serial.

    Patches SerialManager and SocketServer so create_mcp_server does not
    touch real hardware or filesystem. Yields (mcp, buffer_manager, serial_mock).
    """
    import ttygeist.server as srv

    serial_mock = _serial_mock()
    buf = BufferManager(max_size_bytes=4096, line_limit=100)

    with (
        patch.object(srv, "SerialManager", return_value=serial_mock),
        patch.object(srv, "SocketServer") as mock_ss_cls,
        patch("signal.signal"),
        patch("atexit.register"),
    ):
        mock_ss_cls.return_value = MagicMock()
        cfg = MagicMock()
        cfg.data = {"server": {"name": "test"}}
        cfg.buffer_max_size_bytes = 4096
        cfg.buffer_line_limit = 100
        cfg.serial_port = "/dev/ttyTEST"
        cfg.serial_baudrate = 115200
        cfg.serial_bytesize = 8
        cfg.serial_parity = "N"
        cfg.serial_stopbits = 1
        cfg.serial_timeout = 1.0
        cfg.serial_write_timeout = 1.0
        cfg.reconnect_delay = 2.0
        cfg.max_reconnect_delay = 30.0
        cfg.reconnect_backoff_multiplier = 1.5
        cfg.socket_path = "/tmp/test.sock"

        mcp = srv.create_mcp_server(cfg)
        # Override buffer_manager with our own so we can prepopulate it
        srv.buffer_manager = buf

        yield mcp, buf, serial_mock

    # Reset module globals
    srv.buffer_manager = None
    srv.serial_manager = None
    srv.socket_server = None
    srv._shutdown_executed = False


class TestSerialRead:
    @pytest.mark.asyncio
    async def test_read_all(self, mcp_env):
        mcp, buf, _ = mcp_env
        buf.append("line1")
        buf.append("line2")
        result = await _get_tool(mcp, "serial_read")()
        assert result["success"] is True
        assert result["lines_read"] == 2
        assert result["data"][0]["data"] == "line1"

    @pytest.mark.asyncio
    async def test_read_with_limit(self, mcp_env):
        mcp, buf, _ = mcp_env
        for i in range(10):
            buf.append(f"line{i}")
        result = await _get_tool(mcp, "serial_read")(lines=3)
        assert result["success"] is True
        assert result["lines_read"] == 3
        assert result["data"][0]["data"] == "line7"

    @pytest.mark.asyncio
    async def test_read_clear_after(self, mcp_env):
        mcp, buf, _ = mcp_env
        buf.append("gone")
        result = await _get_tool(mcp, "serial_read")(clear_after_read=True)
        assert result["success"] is True
        assert result["lines_read"] == 1
        assert buf.read() == []


class TestBufferClear:
    @pytest.mark.asyncio
    async def test_clear(self, mcp_env):
        mcp, buf, _ = mcp_env
        buf.append("data1")
        buf.append("data2")
        result = await _get_tool(mcp, "buffer_clear")()
        assert result["success"] is True
        assert result["lines_cleared"] == 2
        assert buf.read() == []


class TestBufferInspect:
    @pytest.mark.asyncio
    async def test_inspect(self, mcp_env):
        mcp, buf, _ = mcp_env
        for i in range(5):
            buf.append(f"line{i}")
        result = await _get_tool(mcp, "buffer_inspect")(tail_lines=3)
        assert result["success"] is True
        assert result["tail_lines"] == 3
        assert result["data"][0]["data"] == "line2"
        assert result["statistics"]["current_lines"] == 5


class TestSerialWrite:
    @pytest.mark.asyncio
    async def test_write_delegates_to_manager(self, mcp_env):
        mcp, _, serial_mock = mcp_env
        result = await _get_tool(mcp, "serial_write")(data="cmd", add_newline=True)
        assert result["success"] is True
        assert result["bytes_written"] == 7
        serial_mock.write.assert_called_once_with(data="cmd", add_newline=True)


class TestSerialReconnect:
    @pytest.mark.asyncio
    async def test_reconnect(self, mcp_env):
        mcp, _, serial_mock = mcp_env
        result = await _get_tool(mcp, "serial_reconnect")()
        assert result["success"] is True
        assert result["connected"] is True
        serial_mock.reconnect.assert_called_once()


class TestSerialStatus:
    @pytest.mark.asyncio
    async def test_status(self, mcp_env):
        mcp, _, serial_mock = mcp_env
        result = await _get_tool(mcp, "serial_status")()
        assert result["serial"]["connected"] is True
        assert result["serial"]["port"] == "/dev/ttyTEST"
        assert "current_lines" in result["buffer"]
        serial_mock.get_status.assert_called_once()
