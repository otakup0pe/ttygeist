"""Tests for DeviceRegistry."""

from unittest.mock import MagicMock, patch

import pytest

from ttygeist.buffer_manager import BufferManager
from ttygeist.config import Config, DeviceConfig
from ttygeist.device_registry import (
    AmbiguousDeviceError,
    DeviceNotFoundError,
    DeviceRegistry,
    FirmwareInfo,
    USBInfo,
    compile_firmware_patterns,
)


def _make_port_info(
    device="/dev/ttyACM0",
    vid=0x303A,
    pid=0x1001,
    serial_number="AA:BB:CC:DD:EE:FF",
    description="ESP32",
    hwid="USB",
    location="1-2",
    manufacturer="Espressif",
):
    """Create a mock port_info object mimicking serial.tools.list_ports."""
    mock = MagicMock()
    mock.device = device
    mock.vid = vid
    mock.pid = pid
    mock.serial_number = serial_number
    mock.description = description
    mock.hwid = hwid
    mock.location = location
    mock.manufacturer = manufacturer
    return mock


class TestDeviceRegistry:
    def test_creates_entries_from_config(self):
        cfg = Config(
            devices={
                "dev1": DeviceConfig(name="dev1", serial="SERIAL1"),
                "dev2": DeviceConfig(name="dev2", port="/dev/ttyS0"),
            }
        )
        reg = DeviceRegistry(cfg)
        assert "dev1" in reg.devices
        assert "dev2" in reg.devices
        assert reg.devices["dev1"].config.serial == "SERIAL1"

    def test_scan_matches_by_serial(self):
        cfg = Config(
            devices={
                "mydev": DeviceConfig(name="mydev", serial="AA:BB:CC:DD:EE:FF"),
            }
        )
        reg = DeviceRegistry(cfg)
        port = _make_port_info(serial_number="AA:BB:CC:DD:EE:FF")

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()

        assert "mydev" in result["matched"]
        assert reg.devices["mydev"].port == "/dev/ttyACM0"
        assert reg.devices["mydev"].usb_info.vid == 0x303A

    def test_scan_blocks_serial_numbers(self):
        cfg = Config(
            devices={},
            block=["BLOCKED_SERIAL"],
        )
        reg = DeviceRegistry(cfg)
        port = _make_port_info(serial_number="BLOCKED_SERIAL")

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()

        assert len(result["blocked"]) == 1
        assert result["blocked"][0]["serial"] == "BLOCKED_SERIAL"
        assert "BLOCKED_SERIAL" not in [e.config.serial for e in reg.devices.values()]

    def test_scan_auto_discovers_unnamed(self):
        cfg = Config(devices={})
        reg = DeviceRegistry(cfg)
        port = _make_port_info(serial_number="NEW_DEVICE", description="CH340")

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()

        assert len(result["new"]) == 1
        auto_name = result["new"][0]
        assert auto_name in reg.devices
        assert reg.devices[auto_name].auto_discovered is True

    def test_scan_named_takes_priority(self):
        """Named device should match before auto-discovery creates a new entry."""
        cfg = Config(
            devices={
                "mydev": DeviceConfig(name="mydev", serial="KNOWN"),
            }
        )
        reg = DeviceRegistry(cfg)
        port = _make_port_info(serial_number="KNOWN")

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()

        assert "mydev" in result["matched"]
        assert len(result["new"]) == 0


class TestResolve:
    def test_resolve_by_name(self):
        cfg = Config(
            devices={
                "dev1": DeviceConfig(name="dev1", serial="S1"),
            }
        )
        reg = DeviceRegistry(cfg)
        entry = reg.resolve("dev1")
        assert entry.name == "dev1"

    def test_resolve_implicit_single(self):
        cfg = Config(
            devices={
                "only": DeviceConfig(name="only", serial="S1"),
            }
        )
        reg = DeviceRegistry(cfg)
        entry = reg.resolve(None)
        assert entry.name == "only"

    def test_resolve_implicit_ambiguous(self):
        cfg = Config(
            devices={
                "a": DeviceConfig(name="a", serial="S1"),
                "b": DeviceConfig(name="b", serial="S2"),
            }
        )
        reg = DeviceRegistry(cfg)
        with pytest.raises(AmbiguousDeviceError) as exc_info:
            reg.resolve(None)
        assert "a" in exc_info.value.available
        assert "b" in exc_info.value.available

    def test_resolve_not_found(self):
        cfg = Config(
            devices={
                "a": DeviceConfig(name="a", serial="S1"),
            }
        )
        reg = DeviceRegistry(cfg)
        with pytest.raises(DeviceNotFoundError) as exc_info:
            reg.resolve("nope")
        assert exc_info.value.name == "nope"
        assert "a" in exc_info.value.available


class TestStatus:
    def test_status_includes_all_devices(self):
        cfg = Config(
            devices={
                "a": DeviceConfig(name="a", serial="S1"),
                "b": DeviceConfig(name="b", port="/dev/ttyS0"),
            }
        )
        reg = DeviceRegistry(cfg)
        status = reg.status()
        assert "a" in status["devices"]
        assert "b" in status["devices"]
        assert status["block_list"] == []


class TestFirmwarePatterns:
    def test_compile_patterns(self):
        raw = [
            {"pattern": r"Version:\s*(.+)", "field": "firmware_version"},
            {"pattern": r"MyDevice Boot", "product": "mydevice"},
        ]
        compiled = compile_firmware_patterns(raw)
        assert len(compiled) == 2
        # First pattern should match
        m = compiled[0][0].search("Version: 1.2.3")
        assert m is not None
        assert m.group(1) == "1.2.3"

    def test_empty_patterns(self):
        assert compile_firmware_patterns([]) == []

    def test_scan_firmware_identifies_product(self):
        cfg = Config(
            devices={"dev": DeviceConfig(name="dev", serial="S1")},
            firmware_patterns=[
                {"pattern": r"MyDevice Boot", "product": "mydevice"},
                {"pattern": r"Version:\s*(.+)", "field": "firmware_version"},
            ],
        )
        reg = DeviceRegistry(cfg)
        entry = reg.devices["dev"]

        # Simulate a buffer with boot output
        entry.buffer_manager = BufferManager(max_size_bytes=4096, line_limit=100)
        entry.serial_manager = MagicMock()
        entry.serial_manager.is_connected = True
        entry.buffer_manager.append("Starting up...")
        entry.buffer_manager.append("MyDevice Boot")
        entry.buffer_manager.append("Version: 2026.05.20")

        reg._scan_firmware(entry)
        assert entry.firmware_info.product == "mydevice"
        assert entry.firmware_info.firmware_version == "2026.05.20"
        assert entry._fw_scan_done is True

    def test_no_patterns_skips_scan(self):
        cfg = Config(
            devices={"dev": DeviceConfig(name="dev", serial="S1")},
            firmware_patterns=[],
        )
        reg = DeviceRegistry(cfg)
        entry = reg.devices["dev"]
        entry.buffer_manager = BufferManager(max_size_bytes=4096, line_limit=100)
        entry.serial_manager = MagicMock()
        entry.serial_manager.is_connected = True
        entry.buffer_manager.append("some output")

        # Should not crash or set anything
        reg._scan_firmware(entry)
        assert entry.firmware_info.product is None


class TestUnplugSweep:
    """Verify that unplugged devices are cleaned up on rescan."""

    def test_auto_discovered_removed_on_unplug(self):
        """Auto-discovered device is removed from registry when unplugged."""
        cfg = Config(devices={})
        reg = DeviceRegistry(cfg)
        port = _make_port_info(serial_number="DYNAMIC1", description="CH340")

        # First scan: device appears
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()
        assert len(result["new"]) == 1
        auto_name = result["new"][0]
        assert auto_name in reg.devices

        # Simulate connection by attaching a mock serial manager
        mock_sm = MagicMock()
        mock_sm.is_connected = True
        reg.devices[auto_name].serial_manager = mock_sm

        # Second scan: device gone
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[]):
            result = reg.scan()

        assert auto_name not in reg.devices
        assert auto_name in result.get("removed", [])
        mock_sm.stop.assert_called_once()

    def test_named_device_keeps_entry_on_unplug(self):
        """Named device (from config) keeps its entry but loses port/usb_info."""
        cfg = Config(
            devices={
                "mydev": DeviceConfig(name="mydev", serial="AA:BB:CC:DD:EE:FF"),
            }
        )
        reg = DeviceRegistry(cfg)
        port = _make_port_info(serial_number="AA:BB:CC:DD:EE:FF")

        # First scan: device matched
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()
        assert "mydev" in result["matched"]
        assert reg.devices["mydev"].port == "/dev/ttyACM0"

        # Attach mock serial manager
        mock_sm = MagicMock()
        mock_sm.is_connected = True
        reg.devices["mydev"].serial_manager = mock_sm

        # Second scan: device gone
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[]):
            result = reg.scan()

        # Entry persists but port and usb_info cleared
        assert "mydev" in reg.devices
        assert reg.devices["mydev"].port is None
        assert reg.devices["mydev"].usb_info is None
        assert reg.devices["mydev"].serial_manager is None
        mock_sm.stop.assert_called_once()

    def test_named_device_reconnects_on_replug(self):
        """Named device re-matches when plugged back in after unplug."""
        cfg = Config(
            devices={
                "mydev": DeviceConfig(name="mydev", serial="AA:BB:CC:DD:EE:FF"),
            }
        )
        reg = DeviceRegistry(cfg)
        port = _make_port_info(serial_number="AA:BB:CC:DD:EE:FF")

        # Scan 1: device present
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            reg.scan()
        assert reg.devices["mydev"].port == "/dev/ttyACM0"

        # Scan 2: device gone
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[]):
            reg.scan()
        assert reg.devices["mydev"].port is None

        # Scan 3: device back (possibly different port path)
        port2 = _make_port_info(device="/dev/ttyACM1", serial_number="AA:BB:CC:DD:EE:FF")
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port2]):
            result = reg.scan()
        assert "mydev" in result["matched"]
        assert reg.devices["mydev"].port == "/dev/ttyACM1"

    def test_explicit_port_device_not_swept(self):
        """Non-USB device with explicit port: config is not affected by sweep."""
        cfg = Config(devices={"gps": DeviceConfig(name="gps", port="/dev/ttyS1")})
        reg = DeviceRegistry(cfg)
        # Set the port like connect_all would
        reg.devices["gps"].port = "/dev/ttyS1"
        mock_sm = MagicMock()
        mock_sm.is_connected = True
        reg.devices["gps"].serial_manager = mock_sm

        # Scan with no USB ports -- should NOT touch the explicit port device
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[]):
            reg.scan()

        assert reg.devices["gps"].port == "/dev/ttyS1"
        assert reg.devices["gps"].serial_manager is mock_sm
        mock_sm.stop.assert_not_called()

    def test_unplug_no_serial_manager(self):
        """Sweep handles devices that never connected (no serial_manager)."""
        cfg = Config(devices={})
        reg = DeviceRegistry(cfg)
        port = _make_port_info(serial_number="EPHEMERAL", description="FTDI")

        # Scan 1: discovered but no serial manager attached
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()
        auto_name = result["new"][0]
        assert reg.devices[auto_name].serial_manager is None

        # Scan 2: gone -- should remove cleanly without crash
        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[]):
            result = reg.scan()
        assert auto_name not in reg.devices
        assert auto_name in result.get("removed", [])


class TestUSBInfo:
    def test_to_dict_filters_none(self):
        info = USBInfo(vid=0x303A, pid=0x1001, serial_number="ABC")
        d = info.to_dict()
        assert d["vid"] == "0x303a"
        assert d["serial_number"] == "ABC"
        assert "location" not in d  # None values excluded


class TestFirmwareInfo:
    def test_to_dict_filters_none(self):
        info = FirmwareInfo(product="test", firmware_version="1.0")
        d = info.to_dict()
        assert d["product"] == "test"
        assert "hardware_id" not in d


class TestGhostPortFiltering:
    """Verify that phantom legacy ttyS* ports are filtered from auto-discovery."""

    def _ghost_ports(self, count=4):
        """Generate mock ghost ttyS ports like the kernel enumerates."""
        return [
            _make_port_info(
                device=f"/dev/ttyS{i}",
                vid=None,
                pid=None,
                serial_number=None,
                description="n/a",
                hwid=f"PNP050{i}",
                location=None,
                manufacturer=None,
            )
            for i in range(count)
        ]

    def test_ghost_ttys_filtered_from_autodiscovery(self):
        """Ghost ttyS ports with no VID/PID and 'n/a' description are skipped."""
        cfg = Config(devices={})
        reg = DeviceRegistry(cfg)
        ghosts = self._ghost_ports(4)

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=ghosts):
            result = reg.scan()

        assert result["new"] == []
        assert result["matched"] == []
        # No auto-discovered entries should have been created
        assert len(reg.devices) == 0

    def test_real_usb_devices_still_discovered(self):
        """Real USB devices with VID/PID pass through the filter."""
        cfg = Config(devices={})
        reg = DeviceRegistry(cfg)
        ghosts = self._ghost_ports(4)
        real_usb = _make_port_info(
            device="/dev/ttyACM0",
            vid=0x303A,
            pid=0x1001,
            serial_number="AA:BB:CC:DD:EE:FF",
            description="ESP32",
        )
        all_ports = ghosts + [real_usb]

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=all_ports):
            result = reg.scan()

        # Only the real USB device should be auto-discovered
        assert len(result["new"]) == 1
        auto_name = result["new"][0]
        assert reg.devices[auto_name].port == "/dev/ttyACM0"
        assert reg.devices[auto_name].usb_info.vid == 0x303A

    def test_explicit_port_config_ttys_still_works(self):
        """Named devices with explicit port: on ttyS* bypass the ghost filter."""
        cfg = Config(
            devices={
                "real-uart": DeviceConfig(name="real-uart", port="/dev/ttyS1"),
            }
        )
        reg = DeviceRegistry(cfg)
        ghosts = self._ghost_ports(4)

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=ghosts):
            result = reg.scan()

        # Ghost ports filtered from auto-discovery
        assert result["new"] == []
        # But the explicitly configured device is still in the registry
        assert "real-uart" in reg.devices
        assert reg.devices["real-uart"].config.port == "/dev/ttyS1"

    def test_ghost_filter_empty_description(self):
        """Ports with empty string description are also treated as ghosts."""
        cfg = Config(devices={})
        reg = DeviceRegistry(cfg)
        port = _make_port_info(
            device="/dev/ttyS0",
            vid=None,
            pid=None,
            serial_number=None,
            description="",
            hwid="PNP0500",
            location=None,
            manufacturer=None,
        )

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()

        assert result["new"] == []
        assert len(reg.devices) == 0

    def test_non_ttys_non_usb_not_filtered(self):
        """Non-USB ports that are NOT ttyS* pattern should still be discovered."""
        cfg = Config(devices={})
        reg = DeviceRegistry(cfg)
        # e.g. a Bluetooth serial port or other non-USB, non-ttyS device
        port = _make_port_info(
            device="/dev/rfcomm0",
            vid=None,
            pid=None,
            serial_number=None,
            description="n/a",
            hwid="",
            location=None,
            manufacturer=None,
        )

        with patch("ttygeist.device_registry.serial.tools.list_ports.comports", return_value=[port]):
            result = reg.scan()

        # Should NOT be filtered -- it's not ttyS*
        assert len(result["new"]) == 1
