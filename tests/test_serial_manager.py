"""Tests for SerialManager with mocked pyserial."""

import time
from unittest.mock import MagicMock, patch

import pytest
from serial.serialutil import SerialException

from ttygeist.buffer_manager import BufferManager
from ttygeist.serial_manager import SerialManager


def _make_manager(**overrides) -> SerialManager:
    """Build a SerialManager with sensible test defaults."""
    buf = overrides.pop("buffer_manager", BufferManager(max_size_bytes=4096, line_limit=100))
    defaults = dict(
        port="/dev/ttyTEST",
        baudrate=115200,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=0.1,
        write_timeout=0.1,
        dtr=True,
        rts=False,
        reconnect_delay=0.1,
        max_reconnect_delay=1.0,
        reconnect_backoff_multiplier=1.5,
        buffer_manager=buf,
    )
    defaults.update(overrides)
    return SerialManager(**defaults)


def _fake_serial(data: bytes = b"", is_open: bool = True):
    """Create a mock serial.Serial object."""
    mock = MagicMock()
    mock.is_open = is_open
    mock.in_waiting = 0
    mock.dtr = False
    mock.rts = False

    read_data = bytearray(data)

    def _read(size=1):
        chunk = bytes(read_data[:size])
        del read_data[:size]
        return chunk

    def _write(data):
        return len(data)

    mock.read = MagicMock(side_effect=_read)
    mock.write = MagicMock(side_effect=_write)
    mock.flush = MagicMock()
    mock.close = MagicMock()
    return mock


@pytest.fixture()
def connected_manager():
    """A SerialManager wired to a fake serial port in connected state.

    Returns (manager, mock_port).
    """
    mgr = _make_manager()
    mgr.is_connected = True
    mock_port = _fake_serial()
    mgr.serial_port = mock_port
    return mgr, mock_port


def _read_loop_harness(data_chunks):
    """Set up a SerialManager ready to run _read_loop with canned data.

    The last element of data_chunks should be b"" to signal end-of-data.
    Returns (manager, buffer_manager).
    """
    buf = BufferManager(max_size_bytes=4096, line_limit=100)
    mgr = _make_manager(buffer_manager=buf)
    call_count = 0

    def fake_read(size=1):
        nonlocal call_count
        if call_count < len(data_chunks):
            chunk = data_chunks[call_count]
            call_count += 1
            if not chunk:
                mgr.is_connected = False
            return chunk
        mgr.is_connected = False
        return b""

    mock_port = _fake_serial()
    mock_port.read = fake_read
    mock_port.in_waiting = 0

    mgr.serial_port = mock_port
    mgr.is_connected = True
    mgr.is_running = True
    mgr.connection_start_time = time.time() - 10  # Past grace period

    return mgr, buf


# ---------------------- Write ----------------------


class TestWrite:
    def test_write_when_connected(self, connected_manager):
        mgr, _ = connected_manager
        result = mgr.write("hello")
        assert result["success"] is True
        assert result["bytes_written"] > 0

    def test_write_appends_newline(self, connected_manager):
        mgr, mock_port = connected_manager
        mgr.write("cmd")
        written = mock_port.write.call_args[0][0]
        assert written.endswith(b"\r\n")

    def test_write_no_newline(self, connected_manager):
        mgr, mock_port = connected_manager
        mgr.write("cmd", add_newline=False)
        written = mock_port.write.call_args[0][0]
        assert written == b"cmd"

    def test_write_already_has_newline(self, connected_manager):
        mgr, mock_port = connected_manager
        mgr.write("cmd\r\n", add_newline=True)
        written = mock_port.write.call_args[0][0]
        # Should not double-add newline
        assert written == b"cmd\r\n"

    def test_write_when_disconnected(self):
        mgr = _make_manager()
        mgr.is_connected = False
        result = mgr.write("hello")
        assert result["success"] is False
        assert "Not connected" in result["error"]

    def test_write_port_not_open(self):
        mgr = _make_manager()
        mgr.is_connected = True
        mgr.serial_port = _fake_serial(is_open=False)
        result = mgr.write("hello")
        assert result["success"] is False

    def test_write_serial_exception(self, connected_manager):
        mgr, mock_port = connected_manager
        mock_port.write.side_effect = SerialException("write failed")
        result = mgr.write("hello")
        assert result["success"] is False
        assert mgr.error_count == 1
        assert mgr.is_connected is False


# ---------------------- Status ----------------------


class TestGetStatus:
    def test_status_fields(self):
        mgr = _make_manager()
        status = mgr.get_status()
        assert status["port"] == "/dev/ttyTEST"
        assert status["baudrate"] == 115200
        assert status["connected"] is False
        assert status["error_count"] == 0
        assert status["reconnect_count"] == 0
        assert status["uptime_seconds"] is None

    def test_status_after_connect(self):
        mgr = _make_manager()
        mgr.is_connected = True
        mgr.connection_start_time = time.time() - 10
        status = mgr.get_status()
        assert status["connected"] is True
        assert status["uptime_seconds"] >= 9


# ---------------------- Raw listeners ----------------------


class TestRawListeners:
    def test_add_remove_listener(self):
        mgr = _make_manager()
        q = mgr.add_raw_listener()
        assert q in mgr._raw_listeners
        mgr.remove_raw_listener(q)
        assert q not in mgr._raw_listeners

    def test_broadcast_raw(self):
        mgr = _make_manager()
        q = mgr.add_raw_listener()
        mgr._broadcast_raw(b"hello")
        assert q.get_nowait() == b"hello"

    def test_broadcast_empty_ignored(self):
        mgr = _make_manager()
        q = mgr.add_raw_listener()
        mgr._broadcast_raw(b"")
        assert q.empty()

    def test_broadcast_full_queue_no_crash(self):
        mgr = _make_manager()
        q = mgr.add_raw_listener()
        # Fill it up
        for _ in range(1000):
            q.put_nowait(b"x")
        # Should not raise
        mgr._broadcast_raw(b"overflow")
        # Queue is still full (item was dropped)
        assert q.qsize() == 1000

    def test_remove_drains_queue(self):
        mgr = _make_manager()
        q = mgr.add_raw_listener()
        q.put(b"data1")
        q.put(b"data2")
        mgr.remove_raw_listener(q)
        assert q.empty()


# ---------------------- Connection ----------------------


class TestConnect:
    @patch("ttygeist.serial_manager.serial.Serial")
    def test_connect_success(self, mock_serial_cls):
        mock_port = _fake_serial()
        mock_serial_cls.return_value = mock_port
        mgr = _make_manager()
        result = mgr._connect()
        assert result is True
        assert mgr.is_connected is True
        assert mgr.serial_port is mock_port
        # Verify DTR/RTS are set per config defaults (dtr=True, rts=False)
        assert mock_port.dtr is True
        assert mock_port.rts is False

    @patch("ttygeist.serial_manager.serial.Serial")
    def test_connect_failure(self, mock_serial_cls):
        mock_serial_cls.side_effect = SerialException("no device")
        mgr = _make_manager()
        result = mgr._connect()
        assert result is False
        assert mgr.is_connected is False
        assert mgr.error_count == 1
        assert "no device" in mgr.last_error

    def test_connect_already_open(self):
        mgr = _make_manager()
        mgr.serial_port = _fake_serial(is_open=True)
        result = mgr._connect()
        assert result is True

    def test_connect_already_open_sets_is_connected(self):
        """Fast-path must set is_connected to prevent monitor loop spin."""
        mgr = _make_manager()
        mgr.serial_port = _fake_serial(is_open=True)
        mgr.is_connected = False
        result = mgr._connect()
        assert result is True
        assert mgr.is_connected is True

    @patch("ttygeist.serial_manager.serial.Serial")
    def test_connect_dtr_rts_from_config(self, mock_serial_cls):
        mock_port = _fake_serial()
        mock_serial_cls.return_value = mock_port
        mgr = _make_manager(dtr=False, rts=True)
        result = mgr._connect()
        assert result is True
        assert mock_port.dtr is False
        assert mock_port.rts is True


class TestDisconnect:
    def test_disconnect_closes_port(self):
        mgr = _make_manager()
        mock_port = _fake_serial()
        mgr.serial_port = mock_port
        mgr.is_connected = True
        mgr._disconnect()
        mock_port.close.assert_called_once()
        assert mgr.serial_port is None
        assert mgr.is_connected is False

    def test_disconnect_no_port(self):
        mgr = _make_manager()
        mgr._disconnect()  # Should not raise


# ---------------------- Read loop line parsing ----------------------


class TestReadLoopParsing:
    """Test read loop indirectly by driving it with mock serial data."""

    def test_basic_line_buffering(self):
        mgr, buf = _read_loop_harness([b"hello\r\n", b"world\r\n", b""])
        mgr._read_loop()
        data = [line.data for line in buf.read()]
        assert "hello" in data
        assert "world" in data

    def test_crlf_splitting(self):
        """The read loop prioritizes \\r\\n over \\n or \\r alone."""
        mgr, buf = _read_loop_harness([b"aaa\r\nbbb\r\nccc\r\n", b""])
        mgr._read_loop()
        data = [line.data for line in buf.read()]
        assert data == ["aaa", "bbb", "ccc"]

    def test_lf_only_splitting(self):
        """Lines separated by bare \\n are split individually."""
        mgr, buf = _read_loop_harness([b"x\ny\nz\n", b""])
        mgr._read_loop()
        data = [line.data for line in buf.read()]
        assert data == ["x", "y", "z"]

    def test_serial_exception_in_read_disconnects(self):
        mgr, buf = _read_loop_harness([b""])
        # Override read to throw instead of returning data
        mgr.serial_port.read = MagicMock(side_effect=SerialException("device gone"))
        mgr._read_loop()
        assert mgr.is_connected is False
        assert mgr.error_count == 1

    def test_raw_broadcast_during_read(self):
        mgr, buf = _read_loop_harness([b"hi\n", b""])
        raw_q = mgr.add_raw_listener()
        mgr._read_loop()
        assert raw_q.get_nowait() == b"hi\n"


# ---------------------- Lifecycle ----------------------


class TestLifecycle:
    @patch("ttygeist.serial_manager.serial.Serial")
    def test_start_stop(self, mock_serial_cls):
        mgr = _make_manager()
        mgr.start()
        assert mgr.is_running is True
        assert mgr.monitor_thread is not None
        assert mgr.monitor_thread.is_alive()
        mgr.stop()
        assert mgr.is_running is False

    def test_double_start(self):
        mgr = _make_manager()
        mgr.is_running = True
        # Should not create another thread
        mgr.start()


# ---------------------- Reconnect ----------------------


class TestReconnect:
    @patch("ttygeist.serial_manager.serial.Serial")
    def test_reconnect_success(self, mock_serial_cls):
        mock_port = _fake_serial()
        mock_serial_cls.return_value = mock_port
        mgr = _make_manager()
        # Simulate existing connection
        mgr.serial_port = _fake_serial()
        mgr.is_connected = True
        result = mgr.reconnect()
        assert result["success"] is True
        assert result["connected"] is True

    @patch("ttygeist.serial_manager.serial.Serial")
    def test_reconnect_failure(self, mock_serial_cls):
        mock_serial_cls.side_effect = SerialException("gone")
        mgr = _make_manager()
        result = mgr.reconnect()
        assert result["success"] is False


# ---------------------- Suspend / Resume ----------------------


class TestSuspendResume:
    def test_suspend_disconnects_and_sets_flag(self, connected_manager):
        mgr, mock_port = connected_manager
        result = mgr.suspend()
        assert result["success"] is True
        assert result["already_suspended"] is False
        assert result["connected"] is False
        assert mgr._suspended is True
        assert mgr.is_connected is False
        mock_port.close.assert_called_once()

    def test_suspend_idempotent(self, connected_manager):
        mgr, _ = connected_manager
        mgr.suspend()
        result = mgr.suspend()
        assert result["success"] is True
        assert result["already_suspended"] is True

    @patch("ttygeist.serial_manager.serial.Serial")
    def test_resume_reconnects_and_clears_flag(self, mock_serial_cls):
        mock_port = _fake_serial()
        mock_serial_cls.return_value = mock_port
        mgr = _make_manager()
        mgr._suspended = True
        result = mgr.resume()
        assert result["success"] is True
        assert result["connected"] is True
        assert mgr._suspended is False

    def test_resume_when_not_suspended(self, connected_manager):
        mgr, _ = connected_manager
        result = mgr.resume()
        assert result["success"] is True
        assert result["already_resumed"] is True

    @patch("ttygeist.serial_manager.serial.Serial")
    def test_resume_failure(self, mock_serial_cls):
        mock_serial_cls.side_effect = SerialException("gone")
        mgr = _make_manager()
        mgr._suspended = True
        result = mgr.resume()
        assert result["success"] is False
        assert mgr._suspended is False  # Flag cleared even on failure

    def test_monitor_loop_skips_reconnect_while_suspended(self):
        """Monitor loop should not attempt connection while suspended."""
        mgr = _make_manager()
        mgr.is_running = True
        mgr._suspended = True
        mgr.is_connected = False

        # Run one iteration of the monitor loop logic manually.
        # If suspended, it should sleep and continue, not call _connect.
        with patch.object(mgr, "_connect") as mock_connect:
            # Simulate one loop pass: suspended check -> sleep -> stop
            original_sleep = time.sleep

            call_count = 0

            def counting_sleep(duration):
                nonlocal call_count
                call_count += 1
                if call_count >= 2:
                    mgr.is_running = False
                original_sleep(0.01)

            with patch("ttygeist.serial_manager.time.sleep", side_effect=counting_sleep):
                mgr._monitor_loop()

            mock_connect.assert_not_called()
