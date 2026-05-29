"""Device registry for multi-device serial management.

ttygeist discovers all serial ports automatically. Named devices
(from config) get their configured names. Unnamed devices get
auto-generated names. Block list filters out unwanted ports.
Accept list (named devices) pins specific serial numbers to names
with optional per-device overrides.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, field

import serial.tools.list_ports

from ttygeist.buffer_manager import BufferManager
from ttygeist.config import _SERIAL_DEFAULTS, CHIPSET_DEFAULTS, Config, DeviceConfig

logger = logging.getLogger(__name__)


def compile_firmware_patterns(raw_patterns: list[dict]) -> list[tuple]:
    """Compile firmware identification patterns from config.

    Each pattern dict has:
      pattern: regex string
      product: (optional) product name to set on match
      field: (optional) firmware_info field to capture group 1 into

    Example config:
      firmware_patterns:
        - pattern: "Version:\\s*(.+)"
          field: firmware_version
        - pattern: "MyDevice Boot"
          product: mydevice
    """
    compiled = []
    for entry in raw_patterns:
        regex = re.compile(entry["pattern"])
        action = {}
        if "product" in entry:
            action["product"] = entry["product"]
        if "field" in entry:
            action["field"] = entry["field"]
        if action:
            compiled.append((regex, action))
    return compiled


@dataclass
class USBInfo:
    """USB enumeration metadata for a port."""

    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    description: str | None = None
    hwid: str | None = None
    location: str | None = None
    manufacturer: str | None = None

    def to_dict(self) -> dict:
        return {
            k: v
            for k, v in {
                "vid": f"0x{self.vid:04x}" if self.vid else None,
                "pid": f"0x{self.pid:04x}" if self.pid else None,
                "serial_number": self.serial_number,
                "description": self.description,
                "location": self.location,
                "manufacturer": self.manufacturer,
            }.items()
            if v is not None
        }


@dataclass
class FirmwareInfo:
    """Firmware identification from boot log parsing."""

    product: str | None = None
    firmware_version: str | None = None
    hardware_id: str | None = None
    platform: str | None = None
    identified_at: float | None = None

    def to_dict(self) -> dict:
        return {
            k: v
            for k, v in {
                "product": self.product,
                "firmware_version": self.firmware_version,
                "hardware_id": self.hardware_id,
                "platform": self.platform,
            }.items()
            if v is not None
        }


@dataclass
class DeviceEntry:
    """A managed serial device with its runtime state."""

    name: str
    config: DeviceConfig
    port: str | None = None
    serial_manager: object = None  # SerialManager (avoid circular import)
    buffer_manager: BufferManager | None = None
    usb_info: USBInfo | None = None
    firmware_info: FirmwareInfo = field(default_factory=FirmwareInfo)
    auto_discovered: bool = False
    _fw_scan_done: bool = False

    @property
    def connected(self) -> bool:
        if self.serial_manager is None:
            return False
        return self.serial_manager.is_connected

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "port": self.port,
            "connected": self.connected,
        }
        if self.auto_discovered:
            result["auto_discovered"] = True
        if self.config.serial:
            result["serial"] = self.config.serial
        if self.usb_info:
            result["usb"] = self.usb_info.to_dict()
        if self.firmware_info and self.firmware_info.product:
            result["firmware"] = self.firmware_info.to_dict()
        if self.buffer_manager:
            stats = self.buffer_manager.get_stats()
            result["buffer_lines"] = stats["current_lines"]
        return result


class DeviceNotFoundError(Exception):
    """Raised when a named device is not in the registry."""

    def __init__(self, name: str, available: list[str]):
        self.name = name
        self.available = available
        super().__init__(f"Device '{name}' not found. Available: {', '.join(available) if available else '(none)'}")


class AmbiguousDeviceError(Exception):
    """Raised when device param is omitted with multiple devices."""

    def __init__(self, available: list[str]):
        self.available = available
        super().__init__(f"Multiple devices connected. Specify one of: {', '.join(available)}")


def _chipset_defaults_for(vid: int | None, pid: int | None) -> dict:
    """Look up default serial settings for a USB chipset."""
    if vid is not None and pid is not None:
        return dict(CHIPSET_DEFAULTS.get((vid, pid), {}))
    return {}


class DeviceRegistry:
    """Manages named serial device connections.

    Always discovers all serial ports. Named devices (from config)
    get priority matching by serial number. Unnamed devices get
    auto-generated names. Block list excludes specific serial numbers.
    """

    def __init__(self, config: Config):
        self.config = config
        self.devices: dict[str, DeviceEntry] = {}
        self.lock = threading.RLock()
        self._scan_thread: threading.Thread | None = None
        self._scan_running = False
        self._fw_monitor_thread: threading.Thread | None = None
        self._fw_monitor_running = False
        self._fw_patterns = compile_firmware_patterns(config.firmware_patterns)

        # Pre-create entries for explicitly configured devices
        for name, dev_cfg in config.devices.items():
            self.devices[name] = DeviceEntry(
                name=name,
                config=dev_cfg,
                port=dev_cfg.port,
            )

    def scan(self) -> dict:
        """Enumerate serial ports and manage device lifecycle.

        - Named devices matched by serial number
        - Blocked serial numbers skipped
        - Unclaimed ports get auto-generated names
        - Non-USB devices (explicit port:) connected directly

        Returns dict with matched/unmatched/blocked lists.
        """
        ports = serial.tools.list_ports.comports()
        result = {"matched": [], "blocked": [], "new": []}

        with self.lock:
            seen_ports = set()

            for port_info in ports:
                usb_serial = port_info.serial_number
                usb_info = USBInfo(
                    vid=port_info.vid,
                    pid=port_info.pid,
                    serial_number=usb_serial,
                    description=port_info.description,
                    hwid=port_info.hwid,
                    location=port_info.location,
                    manufacturer=port_info.manufacturer,
                )

                # Block list check
                if usb_serial and usb_serial in self.config.block:
                    result["blocked"].append(
                        {
                            "port": port_info.device,
                            "serial": usb_serial,
                            "description": port_info.description,
                        }
                    )
                    continue

                # Try to match named devices by serial number
                matched = False
                for entry in self.devices.values():
                    if entry.config.serial and usb_serial and entry.config.serial == usb_serial:
                        entry.port = port_info.device
                        entry.usb_info = usb_info
                        seen_ports.add(port_info.device)
                        result["matched"].append(entry.name)
                        matched = True
                        logger.info(f"Device '{entry.name}' matched to {port_info.device} (serial: {usb_serial})")
                        break

                if not matched:
                    # Skip ghost ttyS* ports: legacy x86 UART stubs that
                    # show up with no USB identifiers and "n/a" description.
                    # Named devices with explicit port: entries bypass this
                    # filter since they're matched by serial above or
                    # connected directly in connect_all().
                    if self._is_ghost_port(port_info):
                        logger.debug(
                            f"Skipping ghost port {port_info.device} "
                            f"(no VID/PID, description={port_info.description!r})"
                        )
                        continue

                    # Auto-discover: create entry for unclaimed port
                    auto_name = self._auto_name(port_info)
                    if auto_name not in self.devices:
                        # Build config from chipset defaults + global defaults
                        chipset = _chipset_defaults_for(port_info.vid, port_info.pid)
                        merged = {**_SERIAL_DEFAULTS, **chipset}
                        dev_cfg = DeviceConfig(
                            name=auto_name,
                            port=port_info.device,
                            **{
                                k: v
                                for k, v in merged.items()
                                if k in DeviceConfig.__dataclass_fields__ and k not in ("name", "serial", "port")
                            },
                        )
                        entry = DeviceEntry(
                            name=auto_name,
                            config=dev_cfg,
                            port=port_info.device,
                            usb_info=usb_info,
                            auto_discovered=True,
                        )
                        self.devices[auto_name] = entry
                        result["new"].append(auto_name)
                        logger.info(f"Auto-discovered '{auto_name}' at {port_info.device}")
                    else:
                        # Already known auto-discovered device, update port if changed
                        existing = self.devices[auto_name]
                        existing.port = port_info.device
                        existing.usb_info = usb_info
                        result["matched"].append(auto_name)

                    seen_ports.add(port_info.device)

            # Sweep for devices whose port vanished since last scan.
            # Auto-discovered devices get removed entirely.
            # Named devices (from config) keep their entry but lose port/connection.
            # Non-USB devices (explicit port:, no serial:) are not discovered via
            # enumeration so they are never in seen_ports -- skip them.
            stale = []
            for name, entry in self.devices.items():
                if entry.port is None:
                    continue
                if entry.port in seen_ports:
                    continue
                # Skip non-USB devices with explicit port config (not auto-discovered)
                if entry.config.port and not entry.config.serial and not entry.auto_discovered:
                    continue
                # Port not seen in enumeration -- device unplugged
                if entry.serial_manager is not None:
                    entry.serial_manager.stop()
                    entry.serial_manager = None
                    logger.info(f"Device '{name}' disconnected (port {entry.port} gone)")
                if entry.auto_discovered:
                    stale.append(name)
                else:
                    # Named device: clear port, keep entry for re-match on replug
                    entry.port = None
                    entry.usb_info = None

            for name in stale:
                del self.devices[name]
                result.setdefault("removed", []).append(name)
                logger.info(f"Removed auto-discovered device '{name}' (unplugged)")

        return result

    def connect(self, name: str) -> bool:
        """Start serial connection for a named device."""
        from ttygeist.serial_manager import SerialManager

        with self.lock:
            entry = self._get_locked(name)
            if entry.port is None:
                logger.warning(f"Device '{name}' has no port assigned")
                return False

            if entry.serial_manager is not None and entry.connected:
                return True

            if entry.buffer_manager is None:
                entry.buffer_manager = BufferManager(
                    max_size_bytes=self.config.buffer_max_size_bytes,
                    line_limit=self.config.buffer_line_limit,
                )

            cfg = entry.config
            entry.serial_manager = SerialManager(
                port=entry.port,
                baudrate=cfg.baud,
                bytesize=cfg.bytesize,
                parity=cfg.parity,
                stopbits=cfg.stopbits,
                timeout=cfg.timeout,
                write_timeout=cfg.write_timeout,
                dtr=cfg.dtr,
                rts=cfg.rts,
                reconnect_delay=cfg.reconnect_delay,
                max_reconnect_delay=cfg.max_reconnect_delay,
                reconnect_backoff_multiplier=cfg.reconnect_backoff_multiplier,
                buffer_manager=entry.buffer_manager,
            )
            entry.serial_manager.start()
            logger.info(f"Device '{name}' connecting to {entry.port} at {cfg.baud} baud")
            return True

    def connect_all(self) -> dict:
        """Scan for ports and connect all matched devices."""
        scan_result = self.scan()
        connected = []
        failed = []

        # Connect all discovered/matched devices
        all_to_connect = scan_result["matched"] + scan_result.get("new", [])
        for name in all_to_connect:
            if self.connect(name):
                connected.append(name)
            else:
                failed.append(name)

        # Also connect non-USB devices (explicit port: entries)
        for name, entry in self.devices.items():
            if entry.config.port and not entry.config.serial and name not in connected and name not in failed:
                entry.port = entry.config.port
                if self.connect(name):
                    connected.append(name)
                else:
                    failed.append(name)

        scan_result["connected"] = connected
        scan_result["failed"] = failed
        return scan_result

    def disconnect(self, name: str):
        """Stop serial connection for a named device."""
        with self.lock:
            entry = self._get_locked(name)
            if entry.serial_manager is not None:
                entry.serial_manager.stop()
                logger.info(f"Device '{name}' disconnected")

    def disconnect_all(self):
        """Stop all serial connections."""
        with self.lock:
            for name, entry in self.devices.items():
                if entry.serial_manager is not None:
                    try:
                        entry.serial_manager.stop()
                    except Exception as e:
                        logger.error(f"Error disconnecting '{name}': {e}")

    def resolve(self, name: str | None = None) -> DeviceEntry:
        """Resolve a device by name, or return the sole device.

        When name is None and only one device exists, returns it.
        When name is None with multiple devices, raises AmbiguousDeviceError.
        """
        with self.lock:
            if name is not None:
                return self._get_locked(name)

            connected = [n for n, e in self.devices.items() if e.connected]
            if len(connected) == 1:
                return self.devices[connected[0]]

            all_names = list(self.devices.keys())
            if len(all_names) == 1:
                return self.devices[all_names[0]]
            if len(all_names) == 0:
                raise DeviceNotFoundError("(none)", [])

            raise AmbiguousDeviceError(all_names)

    def status(self) -> dict:
        """Status for all devices."""
        with self.lock:
            return {
                "devices": {name: entry.to_dict() for name, entry in self.devices.items()},
                "block_list": self.config.block,
            }

    # ---- Background scanning ----

    def start_background_scan(self, interval: float = 5.0):
        """Start background thread that periodically scans for USB changes."""
        if self._scan_thread is not None:
            return
        self._scan_running = True
        self._scan_thread = threading.Thread(
            target=self._background_scan_loop,
            args=(interval,),
            daemon=True,
            name="usb-scan",
        )
        self._scan_thread.start()

    def stop_background_scan(self):
        """Stop the background scan thread."""
        self._scan_running = False
        if self._scan_thread is not None:
            self._scan_thread.join(timeout=5.0)
            self._scan_thread = None

    def _background_scan_loop(self, interval: float):
        """Periodically re-scan USB ports for hot-plug changes."""
        while self._scan_running:
            try:
                result = self.connect_all()
                new = result.get("new", [])
                if new:
                    logger.info(f"Background scan found new devices: {new}")
            except Exception as e:
                logger.error(f"Background scan error: {e}")
            time.sleep(interval)

    # ---- Firmware identification ----

    def start_firmware_monitor(self):
        """Start background thread that scans buffers for firmware ID patterns."""
        if self._fw_monitor_thread is not None:
            return
        self._fw_monitor_running = True
        self._fw_monitor_thread = threading.Thread(
            target=self._firmware_monitor_loop,
            daemon=True,
            name="fw-monitor",
        )
        self._fw_monitor_thread.start()

    def stop_firmware_monitor(self):
        """Stop the firmware monitor thread."""
        self._fw_monitor_running = False
        if self._fw_monitor_thread is not None:
            self._fw_monitor_thread.join(timeout=5.0)
            self._fw_monitor_thread = None

    def _firmware_monitor_loop(self):
        """Periodically scan device buffers for firmware identification."""
        while self._fw_monitor_running:
            with self.lock:
                for entry in self.devices.values():
                    if entry._fw_scan_done or entry.buffer_manager is None:
                        continue
                    if not entry.connected:
                        continue
                    self._scan_firmware(entry)
            time.sleep(2.0)

    def _scan_firmware(self, entry: DeviceEntry):
        """Scan a device's buffer for firmware identification patterns."""
        if not self._fw_patterns:
            return

        lines = entry.buffer_manager.read_tail(lines=100)
        if not lines:
            return

        hits = 0
        for line in lines:
            for pattern, action in self._fw_patterns:
                m = pattern.search(line.data)
                if not m:
                    continue
                if "product" in action:
                    entry.firmware_info.product = action["product"]
                    hits += 1
                elif "field" in action:
                    setattr(entry.firmware_info, action["field"], m.group(1).strip())
                    hits += 1

        if hits > 0:
            entry.firmware_info.identified_at = time.time()
            logger.info(
                f"Device '{entry.name}' identified: "
                f"product={entry.firmware_info.product}, "
                f"version={entry.firmware_info.firmware_version}"
            )
            if entry.firmware_info.product:
                entry._fw_scan_done = True

    # ---- Internal helpers ----

    def _get_locked(self, name: str) -> DeviceEntry:
        """Get device by name (caller must hold self.lock)."""
        if name not in self.devices:
            raise DeviceNotFoundError(name, list(self.devices.keys()))
        return self.devices[name]

    @staticmethod
    def _is_ghost_port(port_info) -> bool:
        """Detect phantom legacy serial ports that shouldn't be auto-discovered.

        Modern x86 systems enumerate up to 32 ttyS0-ttyS31 ports as legacy
        UART stubs even when no hardware exists. These have no USB VID/PID
        and a description of "n/a" or empty. Real USB serial devices always
        have VID/PID set; real hardware UARTs configured via named devices
        with explicit ``port:`` entries bypass auto-discovery entirely.
        """
        if port_info.vid is not None or port_info.pid is not None:
            return False
        desc = port_info.description or ""
        if desc not in ("n/a", ""):
            return False
        device = port_info.device or ""
        return bool(re.match(r"/dev/ttyS\d+$", device))

    def _auto_name(self, port_info) -> str:
        """Generate a name for an auto-discovered device."""
        if port_info.description and port_info.description != "n/a":
            desc = port_info.description.split()[0].lower()
            if port_info.serial_number:
                short_serial = port_info.serial_number[-4:]
                return f"{desc}-{short_serial}"
            return desc
        return port_info.device.replace("/dev/", "")
