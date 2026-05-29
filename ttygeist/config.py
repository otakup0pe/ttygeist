"""ttygeist configuration.

Device-centric config format. ttygeist discovers all serial ports
automatically, mediated by accept/block lists. Each accepted device
is identified by a name and matched by USB serial number or explicit
port path.

Two device forms in YAML:
  Short:    mydevice: "AB:CD:EF:12:34:56"
  Extended: mydevice:
              serial: "AB:CD:EF:12:34:56"
              baud: 460800
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Known USB chipset defaults: VID:PID -> default serial settings.
# Devices matched by chipset inherit these unless overridden.
CHIPSET_DEFAULTS = {
    # Espressif USB Serial/JTAG (ESP32-C3, S3, C6, H2)
    (0x303A, 0x1001): {"baud": 115200, "dtr": True, "rts": False},
    # CH340/CH341 (Meshtastic, cheap dev boards)
    (0x1A86, 0x7523): {"baud": 115200, "dtr": True, "rts": True},
    # CP2102/CP2104 (Meshtastic, quality dev boards)
    (0x10C4, 0xEA60): {"baud": 115200, "dtr": True, "rts": True},
    # FTDI FT232R
    (0x0403, 0x6001): {"baud": 115200, "dtr": True, "rts": True},
    # FTDI FT2232 (dual-port, JTAG adapters)
    (0x0403, 0x6010): {"baud": 115200, "dtr": True, "rts": True},
}

_SERIAL_DEFAULTS = {
    "baud": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 1.0,
    "write_timeout": 1.0,
    "dtr": True,
    "rts": False,
    "reconnect_delay": 2.0,
    "max_reconnect_delay": 30.0,
    "reconnect_backoff_multiplier": 1.5,
}

_BUFFER_DEFAULTS = {
    "max_size_mb": 10,
    "line_limit": 100000,
}

_SERVER_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8443,
    "name": "ttygeist",
    "transport": "stdio",
    "tls_cert": "cert.pem",
    "tls_key": "key.pem",
}

_LOGGING_DEFAULTS = {
    "target": "journal",  # "journal" or "file"
    "file": "ttygeist.log",  # only used when target=file
    "level": "INFO",
    "format": "%(name)s - %(levelname)s - %(message)s",
    "max_size_mb": 50,
    "backup_count": 3,
    "request_log": False,
}

_AUTH_DEFAULTS = {
    "api_keys": [],
    "header_name": "X-API-Key",
    "allow_anon": False,
}

_SOCKET_DEFAULTS = {
    "path": "~/tmp/ttygeist-{pid}.sock",
}


@dataclass
class DeviceConfig:
    """Parsed configuration for a single device."""

    name: str
    serial: str | None = None  # USB serial number to match
    port: str | None = None  # explicit port path (non-USB)
    baud: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    timeout: float = 1.0
    write_timeout: float = 1.0
    dtr: bool = True
    rts: bool = False
    reconnect_delay: float = 2.0
    max_reconnect_delay: float = 30.0
    reconnect_backoff_multiplier: float = 1.5


@dataclass
class Config:
    """Parsed ttygeist configuration."""

    # Device accept/block lists
    devices: dict[str, DeviceConfig] = field(default_factory=dict)
    block: list[str] = field(default_factory=list)

    # Tools that should not be registered
    disabled_tools: list[str] = field(default_factory=list)

    # Firmware identification patterns (regex -> product/field mapping)
    firmware_patterns: list[dict] = field(default_factory=list)

    # Buffer settings
    buffer_max_size_bytes: int = _BUFFER_DEFAULTS["max_size_mb"] * 1024 * 1024
    buffer_line_limit: int = _BUFFER_DEFAULTS["line_limit"]

    # Server settings
    server_host: str = _SERVER_DEFAULTS["host"]
    server_port: int = _SERVER_DEFAULTS["port"]
    server_name: str = _SERVER_DEFAULTS["name"]
    server_transport: str = _SERVER_DEFAULTS["transport"]
    tls_cert_path: str = _SERVER_DEFAULTS["tls_cert"]
    tls_key_path: str = _SERVER_DEFAULTS["tls_key"]

    # Logging
    log_target: str = _LOGGING_DEFAULTS["target"]
    log_file: str = _LOGGING_DEFAULTS["file"]
    log_level: str = _LOGGING_DEFAULTS["level"]
    log_format: str = _LOGGING_DEFAULTS["format"]
    log_max_size_bytes: int = _LOGGING_DEFAULTS["max_size_mb"] * 1024 * 1024
    log_backup_count: int = _LOGGING_DEFAULTS["backup_count"]
    request_log_enabled: bool = _LOGGING_DEFAULTS["request_log"]

    # Auth
    api_keys: list[str] = field(default_factory=list)
    auth_header_name: str = _AUTH_DEFAULTS["header_name"]
    allow_anon: bool = _AUTH_DEFAULTS["allow_anon"]

    # Socket
    socket_path: str = _SOCKET_DEFAULTS["path"]

    @property
    def socket_path_expanded(self) -> str:
        """Socket path with ~ and {pid} expanded."""
        path = os.path.expanduser(self.socket_path)
        return path.replace("{pid}", str(os.getpid()))

    def is_tool_enabled(self, tool_name: str) -> bool:
        """Check if a tool should be registered."""
        return tool_name not in self.disabled_tools


def _parse_device(name: str, value, defaults: dict) -> DeviceConfig:
    """Parse a single device entry (short or extended form)."""
    if isinstance(value, str):
        # Short form: value is the USB serial number
        return DeviceConfig(
            name=name,
            serial=value,
            **{
                k: v
                for k, v in defaults.items()
                if k in DeviceConfig.__dataclass_fields__ and k not in ("name", "serial", "port")
            },
        )

    if isinstance(value, dict):
        params = {
            k: v
            for k, v in defaults.items()
            if k in DeviceConfig.__dataclass_fields__ and k not in ("name", "serial", "port")
        }
        for k, v in value.items():
            if k in DeviceConfig.__dataclass_fields__:
                params[k] = v
        return DeviceConfig(name=name, **params)

    raise ValueError(f"Device '{name}': expected string (serial number) or dict, got {type(value).__name__}")


def load_config(config_path: str | None = None) -> Config:
    """Load and parse ttygeist configuration.

    Args:
        config_path: Path to YAML config file. If None or file missing,
            returns defaults (no named devices, full auto-discovery).
    """
    raw = {}
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    # Serial defaults from config + hardcoded
    serial_defaults = dict(_SERIAL_DEFAULTS)
    if "defaults" in raw and isinstance(raw["defaults"], dict):
        for k, v in raw["defaults"].items():
            if k in _SERIAL_DEFAULTS:
                serial_defaults[k] = v

    # Parse named devices (accept list)
    devices = {}
    raw_devices = raw.get("devices", {})
    if isinstance(raw_devices, dict):
        for name, value in raw_devices.items():
            devices[name] = _parse_device(name, value, serial_defaults)

    # Block list
    block = list(raw.get("block", []))

    # Disabled tools
    disabled_tools = list(raw.get("disabled_tools", []))

    # Firmware identification patterns
    firmware_patterns = list(raw.get("firmware_patterns", []))

    # Buffer
    raw_buffer = {**_BUFFER_DEFAULTS, **raw.get("buffer", {})}

    # Server
    raw_server = {**_SERVER_DEFAULTS, **raw.get("server", {})}

    # Logging
    raw_logging = {**_LOGGING_DEFAULTS, **raw.get("logging", {})}

    # Auth
    raw_auth = {**_AUTH_DEFAULTS, **raw.get("auth", {})}

    # Socket
    raw_socket = {**_SOCKET_DEFAULTS, **raw.get("socket", {})}

    cfg = Config(
        devices=devices,
        block=block,
        disabled_tools=disabled_tools,
        firmware_patterns=firmware_patterns,
        buffer_max_size_bytes=raw_buffer["max_size_mb"] * 1024 * 1024,
        buffer_line_limit=raw_buffer["line_limit"],
        server_host=raw_server["host"],
        server_port=raw_server["port"],
        server_name=raw_server["name"],
        server_transport=raw_server["transport"],
        tls_cert_path=raw_server["tls_cert"],
        tls_key_path=raw_server["tls_key"],
        log_target=raw_logging.get("target", "journal"),
        log_file=raw_logging["file"],
        log_level=raw_logging["level"],
        log_format=raw_logging["format"],
        log_max_size_bytes=raw_logging["max_size_mb"] * 1024 * 1024,
        log_backup_count=raw_logging["backup_count"],
        request_log_enabled=raw_logging.get("request_log", False),
        api_keys=list(raw_auth.get("api_keys", [])),
        auth_header_name=raw_auth["header_name"],
        allow_anon=raw_auth.get("allow_anon", False),
        socket_path=raw_socket["path"],
    )

    _apply_env_overrides(cfg)
    return cfg


def _apply_env_overrides(cfg: Config):
    """Apply TTYGEIST_* environment variable overrides."""
    if "TTYGEIST_API_KEYS" in os.environ:
        keys = [k.strip() for k in os.environ["TTYGEIST_API_KEYS"].split(",") if k.strip()]
        cfg.api_keys.extend(keys)

    if "TTYGEIST_AUTH_HEADER" in os.environ:
        cfg.auth_header_name = os.environ["TTYGEIST_AUTH_HEADER"]

    if "TTYGEIST_ALLOW_ANON" in os.environ:
        cfg.allow_anon = os.environ["TTYGEIST_ALLOW_ANON"].strip().lower() in {"1", "true", "yes"}

    if "TTYGEIST_REQUEST_LOG" in os.environ:
        cfg.request_log_enabled = os.environ["TTYGEIST_REQUEST_LOG"].strip().lower() in {"1", "true", "yes"}

    if "TTYGEIST_TRANSPORT" in os.environ:
        val = os.environ["TTYGEIST_TRANSPORT"].strip().lower()
        if val in {"http", "stdio"}:
            cfg.server_transport = val
