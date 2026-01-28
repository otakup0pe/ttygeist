import logging
import threading
import time
import queue
from typing import Optional, List
import serial
from serial.serialutil import SerialException

from ttygeist.buffer_manager import BufferManager


logger = logging.getLogger(__name__)


class SerialManager:
    """Manages serial port connection with automatic reconnection.

    Provides both line-buffered storage (via BufferManager) and a raw byte
    streaming facility for interactive terminal use.
    """

    def __init__(
        self,
        port: str,
        baudrate: int,
        bytesize: int,
        parity: str,
        stopbits: float,
        timeout: float,
        write_timeout: float,
        reconnect_delay: float,
        max_reconnect_delay: float,
        reconnect_backoff_multiplier: float,
        buffer_manager: BufferManager
    ):
        """Initialize serial manager."""
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.reconnect_backoff_multiplier = reconnect_backoff_multiplier

        self.buffer_manager = buffer_manager
        self.serial_port: Optional[serial.Serial] = None
        self.lock = threading.RLock()

        # State tracking
        self.is_connected = False
        self.is_running = False
        self.connection_start_time: Optional[float] = None
        self.last_error: Optional[str] = None
        self.error_count = 0
        self.reconnect_count = 0

        # Threads
        self.read_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None

        # Raw stream listeners (each is a queue.Queue of bytes objects)
        self._raw_listeners: List[queue.Queue] = []
        self._raw_listeners_lock = threading.Lock()

    # ---------------------- Lifecycle ----------------------
    def start(self):
        if self.is_running:
            logger.warning("Serial manager already running")
            return
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop,
                                               daemon=True)
        self.monitor_thread.start()
        logger.info(f"Serial manager started for port {self.port}")

    def stop(self):
        logger.info("Stopping serial manager...")
        self.is_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(timeout=5)
        self._disconnect()
        logger.info("Serial manager stopped")

    # ---------------------- Raw listener management ----------------------
    def add_raw_listener(self) -> queue.Queue:
        """Register a raw byte stream listener.

        Returns a queue which will receive raw byte chunks as they are read.
        """
        q = queue.Queue(maxsize=1000)  # generous backlog
        with self._raw_listeners_lock:
            self._raw_listeners.append(q)
        return q

    def remove_raw_listener(self, q: queue.Queue):
        with self._raw_listeners_lock:
            if q in self._raw_listeners:
                self._raw_listeners.remove(q)
        # Drain queue to allow garbage collection
        try:
            while not q.empty():
                q.get_nowait()
        except Exception:
            pass

    def _broadcast_raw(self, data: bytes):
        if not data:
            return
        with self._raw_listeners_lock:
            listeners = list(self._raw_listeners)
        for q in listeners:
            try:
                q.put_nowait(data)
            except queue.Full:
                # Drop if listener is too slow
                logger.debug("Raw listener queue full; dropping chunk")
            except Exception as e:
                logger.debug(f"Raw listener enqueue error: {e}")

    # ---------------------- Connection handling ----------------------
    def _connect(self) -> bool:
        try:
            with self.lock:
                if self.serial_port and self.serial_port.is_open:
                    return True
                logger.info(f"Connecting to {self.port} at {self.baudrate} baud...")
                self.serial_port = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    timeout=self.timeout,
                    write_timeout=self.write_timeout,
                    dsrdtr=False,
                    rtscts=False,
                )
                # Explicitly prevent DTR/RTS from triggering board resets
                # Many CircuitPython/Arduino boards reset when DTR is asserted
                self.serial_port.dtr = False
                self.serial_port.rts = False
                self.is_connected = True
                self.connection_start_time = time.time()
                self.last_error = None
                logger.info(f"Connected to {self.port}")
                if self.read_thread and self.read_thread.is_alive():
                    self.read_thread.join(timeout=2)
                self.read_thread = threading.Thread(target=self._read_loop,
                                                    daemon=True)
                self.read_thread.start()
                return True
        except SerialException as e:
            self.last_error = str(e)
            self.error_count += 1
            logger.error(f"Failed to connect to {self.port}: {e}")
            return False
        except Exception as e:
            self.last_error = str(e)
            self.error_count += 1
            logger.error(f"Unexpected error connecting to {self.port}: {e}")
            return False

    def _disconnect(self):
        with self.lock:
            if self.serial_port:
                try:
                    if self.serial_port.is_open:
                        self.serial_port.close()
                        logger.info(f"Disconnected from {self.port}")
                except Exception as e:
                    logger.error(f"Error closing serial port: {e}")
                finally:
                    self.serial_port = None
                    self.is_connected = False

    def _monitor_loop(self):
        current_delay = self.reconnect_delay
        while self.is_running:
            if not self.is_connected:
                if self._connect():
                    current_delay = self.reconnect_delay
                    self.reconnect_count += 1
                else:
                    logger.info(f"Retrying connection in {current_delay:.1f}s...")
                    time.sleep(current_delay)
                    current_delay = min(current_delay * self.reconnect_backoff_multiplier, self.max_reconnect_delay)
            else:
                with self.lock:
                    if self.serial_port and not self.serial_port.is_open:
                        logger.warning("Serial port closed unexpectedly")
                        self.is_connected = False
                        self._disconnect()
                time.sleep(1)

    # ---------------------- Reading & buffering ----------------------
    def _read_loop(self):
        logger.info("Read loop started")
        line_buffer = b""
        # UTF-8 decoder state to handle multi-byte sequences across chunks
        import codecs
        utf8_decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        last_data_time = time.time()
        partial_line_timeout = 1.0
        connection_stable_time = time.time() + 0.5  # Grace period for device initialization

        while self.is_running and self.is_connected:
            try:
                with self.lock:
                    if not self.serial_port or not self.serial_port.is_open:
                        break
                    serial_port = self.serial_port
                chunk = serial_port.read(1)
                if chunk:
                    waiting = serial_port.in_waiting
                    if waiting > 0:
                        chunk += serial_port.read(waiting)

                    # Filter out garbage during initial connection
                    # Many devices send null bytes, initialization sequences, or garbage
                    # when first connecting. Wait for connection to stabilize.
                    current_time = time.time()
                    if current_time < connection_stable_time:
                        # During grace period, filter out suspicious patterns
                        # Keep printable ASCII, common control chars, and valid UTF-8
                        if all(b == 0 or (b < 0x20 and b not in (0x08, 0x09, 0x0A, 0x0D, 0x1B)) for b in chunk):
                            logger.debug(f"Filtered {len(chunk)} garbage bytes during connection init")
                            continue

                    # Broadcast raw chunk BEFORE line parsing
                    self._broadcast_raw(chunk)
                    line_buffer += chunk
                    last_data_time = current_time

                    # Process complete lines
                    while b'\n' in line_buffer or b'\r' in line_buffer:
                        if b'\r\n' in line_buffer:
                            line, line_buffer = line_buffer.split(b'\r\n', 1)
                        elif b'\n' in line_buffer:
                            line, line_buffer = line_buffer.split(b'\n', 1)
                        elif b'\r' in line_buffer:
                            line, line_buffer = line_buffer.split(b'\r', 1)
                        else:
                            break
                        if line:
                            try:
                                # Use incremental decoder for proper multi-byte UTF-8 handling
                                decoded = utf8_decoder.decode(line, final=False)
                                if decoded:
                                    # Strip any remaining control characters except common ones
                                    decoded = ''.join(
                                        c for c in decoded
                                        if c >= ' ' or c in '\t\n\r\x1b' or ord(c) >= 0x80
                                    )
                                    if decoded:
                                        self.buffer_manager.append(decoded)
                                        logger.debug(f"Buffered line: {decoded[:100]}")
                            except Exception as e:
                                logger.error(f"Error decoding serial data: {e}")
                else:
                    # No data; handle partial line flush
                    current_time = time.time()
                    if line_buffer and (current_time - last_data_time) > partial_line_timeout:
                        # Partial line timeout reached. We do NOT rebroadcast raw data here
                        # to avoid duplicating prompts or static fragments (e.g. CircuitPython '>>> ').
                        # Raw bytes were already streamed incrementally when first read.
                        try:
                            # Flush any remaining bytes in the decoder
                            decoded = utf8_decoder.decode(line_buffer, final=True)
                            # Reset decoder for next sequence
                            utf8_decoder.reset()
                            if decoded:
                                # Strip control characters
                                decoded = ''.join(
                                    c for c in decoded
                                    if c >= ' ' or c in '\t\n\r\x1b' or ord(c) >= 0x80
                                )
                                if decoded:
                                    self.buffer_manager.append(decoded)
                                    logger.debug(f"Buffered partial line (no raw re-broadcast): {decoded[:100]}")
                        except Exception as e:
                            logger.error(f"Error decoding partial line: {e}")
                            # Reset decoder on error
                            utf8_decoder.reset()
                        line_buffer = b""
                        last_data_time = current_time
            except SerialException as e:
                logger.error(f"Serial read error: {e}")
                with self.lock:
                    self.last_error = str(e)
                    self.error_count += 1
                    self.is_connected = False
                self._disconnect()
                break
            except Exception as e:
                logger.error(f"Unexpected error in read loop: {e}")
                with self.lock:
                    self.last_error = str(e)
                    self.error_count += 1
        logger.info("Read loop stopped")

    # ---------------------- Writing ----------------------
    def write(self, data: str, add_newline: bool = True) -> dict:
        if not self.is_connected:
            return {'success': False, 'error': 'Not connected', 'bytes_written': 0}
        try:
            with self.lock:
                if not self.serial_port or not self.serial_port.is_open:
                    return {'success': False, 'error': 'Serial port not open', 'bytes_written': 0}
                write_data = data
                if add_newline and not write_data.endswith('\n') and not write_data.endswith('\r\n'):
                    write_data += '\r\n'
                bytes_written = self.serial_port.write(write_data.encode('utf-8'))
                self.serial_port.flush()
                return {'success': True, 'bytes_written': bytes_written, 'data_sent': write_data}
        except SerialException as e:
            self.last_error = str(e)
            self.error_count += 1
            logger.error(f"Serial write error: {e}")
            self.is_connected = False
            return {'success': False, 'error': str(e), 'bytes_written': 0}
        except Exception as e:
            self.last_error = str(e)
            self.error_count += 1
            logger.error(f"Unexpected write error: {e}")
            return {'success': False, 'error': str(e), 'bytes_written': 0}

    # ---------------------- Reconnect & Status ----------------------
    def reconnect(self) -> dict:
        logger.info("Manual reconnection requested")
        self._disconnect()
        time.sleep(0.5)
        success = self._connect()
        return {'success': success, 'connected': self.is_connected, 'error': self.last_error if not success else None}

    def get_status(self) -> dict:
        with self.lock:
            uptime = None
            if self.connection_start_time:
                uptime = round(time.time() - self.connection_start_time, 2)
            return {
                'connected': self.is_connected,
                'port': self.port,
                'baudrate': self.baudrate,
                'bytesize': self.bytesize,
                'parity': self.parity,
                'stopbits': self.stopbits,
                'uptime_seconds': uptime,
                'error_count': self.error_count,
                'reconnect_count': self.reconnect_count,
                'last_error': self.last_error,
            }
