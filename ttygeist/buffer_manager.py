"""Buffer manager for serial data with thread-safe circular buffer."""

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime


@dataclass
class BufferedLine:
    """A single line of serial data with metadata."""
    timestamp: float
    data: str

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            'timestamp': self.timestamp,
            'timestamp_iso': datetime.fromtimestamp(self.timestamp).isoformat(),
            'data': self.data
        }


class BufferManager:
    """Thread-safe circular buffer for serial data."""

    def __init__(self, max_size_bytes: int, line_limit: int):
        """
        Initialize buffer manager.

        Args:
            max_size_bytes: Maximum buffer size in bytes
            line_limit: Maximum number of lines to store
        """
        self.max_size_bytes = max_size_bytes
        self.line_limit = line_limit
        self.buffer: deque[BufferedLine] = deque(maxlen=line_limit)
        self.lock = threading.RLock()

        # Statistics
        self.current_size_bytes = 0
        self.total_lines_received = 0
        self.total_lines_dropped = 0
        self.created_at = time.time()

    def append(self, data: str):
        """
        Append data to buffer.

        Args:
            data: Line of data to append (without trailing newline)
        """
        with self.lock:
            line = BufferedLine(
                timestamp=time.time(),
                data=data
            )

            line_size = len(data.encode('utf-8'))

            # Check if we need to drop lines due to size constraint
            while (self.current_size_bytes + line_size > self.max_size_bytes
                   and len(self.buffer) > 0):
                dropped = self.buffer.popleft()
                self.current_size_bytes -= len(dropped.data.encode('utf-8'))
                self.total_lines_dropped += 1

            # Add the new line
            # Note: deque with maxlen automatically drops oldest if at capacity
            if len(self.buffer) >= self.line_limit:
                dropped = self.buffer.popleft()
                self.current_size_bytes -= len(dropped.data.encode('utf-8'))
                self.total_lines_dropped += 1

            self.buffer.append(line)
            self.current_size_bytes += line_size
            self.total_lines_received += 1

    def read(self, lines: Optional[int] = None,
             clear_after_read: bool = False) -> List[BufferedLine]:
        """
        Read lines from buffer.

        Args:
            lines: Number of lines to read (None = all)
            clear_after_read: Clear buffer after reading

        Returns:
            List of BufferedLine objects
        """
        with self.lock:
            if lines is None:
                result = list(self.buffer)
            else:
                result = list(self.buffer)[-lines:] if lines > 0 else []

            if clear_after_read:
                self.buffer.clear()
                self.current_size_bytes = 0

            return result

    def read_tail(self, lines: int = 50) -> List[BufferedLine]:
        """
        Read last N lines without modifying buffer.

        Args:
            lines: Number of lines to read from end

        Returns:
            List of BufferedLine objects
        """
        with self.lock:
            if lines <= 0:
                return []
            return list(self.buffer)[-lines:]

    def clear(self) -> int:
        """
        Clear all buffered data.

        Returns:
            Number of lines cleared
        """
        with self.lock:
            count = len(self.buffer)
            self.buffer.clear()
            self.current_size_bytes = 0
            return count

    def get_stats(self) -> dict:
        """
        Get buffer statistics.

        Returns:
            Dictionary with buffer stats
        """
        with self.lock:
            uptime = time.time() - self.created_at
            return {
                'current_lines': len(self.buffer),
                'current_size_bytes': self.current_size_bytes,
                'current_size_mb': round(self.current_size_bytes / 1024 / 1024, 2),
                'max_size_bytes': self.max_size_bytes,
                'max_size_mb': round(self.max_size_bytes / 1024 / 1024, 2),
                'line_limit': self.line_limit,
                'utilization_percent': round(
                    (self.current_size_bytes / self.max_size_bytes) * 100, 2
                ),
                'total_lines_received': self.total_lines_received,
                'total_lines_dropped': self.total_lines_dropped,
                'uptime_seconds': round(uptime, 2),
            }
