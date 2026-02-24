"""Tests for BufferManager."""

import threading
import time

import pytest

from ttygeist.buffer_manager import BufferedLine, BufferManager


class TestBufferedLine:
    def test_to_dict(self):
        ts = 1700000000.0
        line = BufferedLine(timestamp=ts, data="hello")
        d = line.to_dict()
        assert d["timestamp"] == ts
        assert d["data"] == "hello"
        assert "timestamp_iso" in d

    def test_to_dict_preserves_data(self):
        line = BufferedLine(timestamp=0.0, data="unicode: \u2603")
        assert line.to_dict()["data"] == "unicode: \u2603"


class TestAppendAndRead:
    def test_append_single(self, small_buffer):
        small_buffer.append("line1")
        result = small_buffer.read()
        assert len(result) == 1
        assert result[0].data == "line1"

    def test_append_multiple(self, small_buffer):
        for i in range(3):
            small_buffer.append(f"line{i}")
        result = small_buffer.read()
        assert len(result) == 3
        assert [r.data for r in result] == ["line0", "line1", "line2"]

    def test_read_with_limit(self, default_buffer):
        for i in range(10):
            default_buffer.append(f"line{i}")
        result = default_buffer.read(lines=3)
        assert len(result) == 3
        assert result[0].data == "line7"

    def test_read_zero_lines(self, default_buffer):
        default_buffer.append("data")
        assert default_buffer.read(lines=0) == []

    def test_read_more_than_available(self, default_buffer):
        default_buffer.append("only")
        result = default_buffer.read(lines=100)
        assert len(result) == 1

    def test_read_all(self, default_buffer):
        for i in range(5):
            default_buffer.append(f"l{i}")
        result = default_buffer.read()
        assert len(result) == 5

    def test_read_clear_after_read(self, default_buffer):
        default_buffer.append("gone")
        result = default_buffer.read(clear_after_read=True)
        assert len(result) == 1
        assert default_buffer.read() == []
        assert default_buffer.current_size_bytes == 0

    def test_read_empty_buffer(self, default_buffer):
        assert default_buffer.read() == []


class TestReadTail:
    def test_read_tail(self, default_buffer):
        for i in range(10):
            default_buffer.append(f"line{i}")
        result = default_buffer.read_tail(3)
        assert [r.data for r in result] == ["line7", "line8", "line9"]

    def test_read_tail_zero(self, default_buffer):
        default_buffer.append("data")
        assert default_buffer.read_tail(0) == []

    def test_read_tail_negative(self, default_buffer):
        default_buffer.append("data")
        assert default_buffer.read_tail(-1) == []

    def test_read_tail_does_not_modify_buffer(self, default_buffer):
        default_buffer.append("keep")
        default_buffer.read_tail(1)
        assert len(default_buffer.read()) == 1


class TestOverflow:
    def test_line_limit_overflow(self):
        buf = BufferManager(max_size_bytes=1024 * 1024, line_limit=3)
        for i in range(5):
            buf.append(f"line{i}")
        result = buf.read()
        assert len(result) == 3
        assert [r.data for r in result] == ["line2", "line3", "line4"]

    def test_line_limit_tracks_dropped(self):
        buf = BufferManager(max_size_bytes=1024 * 1024, line_limit=2)
        for i in range(5):
            buf.append(f"l{i}")
        assert buf.total_lines_dropped == 3
        assert buf.total_lines_received == 5

    def test_byte_limit_overflow(self):
        # Each "aaaa" is 4 bytes. Buffer allows 10 bytes.
        buf = BufferManager(max_size_bytes=10, line_limit=100)
        buf.append("aaaa")  # 4 bytes, total=4
        buf.append("bbbb")  # 4 bytes, total=8
        buf.append("cccc")  # 4 bytes, would be 12 -> drop "aaaa" -> total=8
        result = buf.read()
        assert [r.data for r in result] == ["bbbb", "cccc"]

    def test_byte_limit_drops_multiple(self):
        buf = BufferManager(max_size_bytes=10, line_limit=100)
        buf.append("aaa")  # 3
        buf.append("bbb")  # 3, total=6
        buf.append("ccc")  # 3, total=9
        # Add 9-byte line -> must drop all three to fit (9+any > 10)
        buf.append("xxxxxxxxx")  # 9 bytes
        result = buf.read()
        assert len(result) == 1
        assert result[0].data == "xxxxxxxxx"

    def test_size_tracks_utf8(self):
        buf = BufferManager(max_size_bytes=100, line_limit=100)
        # snowman is 3 bytes in utf-8
        buf.append("\u2603")
        assert buf.current_size_bytes == 3


class TestClear:
    def test_clear_returns_count(self, default_buffer):
        for i in range(4):
            default_buffer.append(f"l{i}")
        assert default_buffer.clear() == 4

    def test_clear_resets_size(self, default_buffer):
        default_buffer.append("data")
        default_buffer.clear()
        assert default_buffer.current_size_bytes == 0

    def test_clear_empty(self, default_buffer):
        assert default_buffer.clear() == 0


class TestStats:
    def test_stats_keys(self, default_buffer):
        stats = default_buffer.get_stats()
        expected = {
            "current_lines",
            "current_size_bytes",
            "current_size_mb",
            "max_size_bytes",
            "max_size_mb",
            "line_limit",
            "utilization_percent",
            "total_lines_received",
            "total_lines_dropped",
            "uptime_seconds",
        }
        assert set(stats.keys()) == expected

    def test_stats_reflect_state(self):
        buf = BufferManager(max_size_bytes=1024, line_limit=10)
        buf.append("hello")
        stats = buf.get_stats()
        assert stats["current_lines"] == 1
        assert stats["current_size_bytes"] == 5
        assert stats["total_lines_received"] == 1
        assert stats["total_lines_dropped"] == 0
        assert stats["max_size_bytes"] == 1024
        assert stats["line_limit"] == 10

    def test_utilization_percent(self):
        buf = BufferManager(max_size_bytes=100, line_limit=1000)
        buf.append("a" * 50)
        stats = buf.get_stats()
        assert stats["utilization_percent"] == 50.0

    def test_uptime_increases(self):
        buf = BufferManager(max_size_bytes=100, line_limit=10)
        time.sleep(0.05)
        stats = buf.get_stats()
        assert stats["uptime_seconds"] >= 0.04


class TestThreadSafety:
    @pytest.mark.timeout(5)
    def test_concurrent_append_read(self):
        buf = BufferManager(max_size_bytes=1024 * 1024, line_limit=10000)
        errors = []

        def writer(n):
            try:
                for i in range(500):
                    buf.append(f"thread{n}-line{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(200):
                    buf.read_tail(10)
                    buf.get_stats()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert buf.total_lines_received == 2000
