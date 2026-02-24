import os
from pathlib import Path

import yaml


class Config:
    def __init__(self, config_path: str | None = None):
        """Load configuration from file or defaults."""
        self.config_path = config_path
        self.data = self._load_config()
        self._apply_env_overrides()

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        if self.config_path and Path(self.config_path).exists():
            with open(self.config_path) as f:
                return yaml.safe_load(f)
        return self._default_config()

    def _default_config(self) -> dict:
        """Return default configuration."""
        return {
            "serial": {
                "port": "/dev/ttyUSB0",
                "baudrate": 115200,
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
            },
            "buffer": {
                "max_size_mb": 10,
                "line_limit": 100000,
                "overflow": "fifo",
            },
            "server": {
                "host": "127.0.0.1",
                "port": 8443,
                "tls_cert": "cert.pem",
                "tls_key": "key.pem",
                "name": "ttygeist",
                "transport": "stdio",
            },
            "logging": {
                "file": "ttygeist.log",
                "level": "INFO",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "max_size_mb": 50,
                "backup_count": 3,
                "request_log": False,
            },
            "auth": {
                "api_keys": [],
                "header_name": "X-API-Key",
                "allow_anon": False,
            },
            "socket": {
                "path": "~/tmp/ttygeist-{pid}.sock",
            },
        }

    def _apply_env_overrides(self):
        """Apply environment variable overrides."""
        # API keys from environment
        env_keys = os.environ.get("TTYGEIST_API_KEYS", "")
        if env_keys:
            keys = [k.strip() for k in env_keys.split(",") if k.strip()]
            self.data["auth"]["api_keys"].extend(keys)

        # Serial port override
        if "TTYGEIST_PORT" in os.environ:
            self.data["serial"]["port"] = os.environ["TTYGEIST_PORT"]

        # Auth header override
        if "TTYGEIST_AUTH_HEADER" in os.environ:
            self.data["auth"]["header_name"] = os.environ["TTYGEIST_AUTH_HEADER"]

        # Allow anonymous override ("1", "true", "yes")
        if "TTYGEIST_ALLOW_ANON" in os.environ:
            val = os.environ["TTYGEIST_ALLOW_ANON"].strip().lower()
            self.data["auth"]["allow_anon"] = val in {"1", "true", "yes"}

        # Request logging override
        if "TTYGEIST_REQUEST_LOG" in os.environ:
            val = os.environ["TTYGEIST_REQUEST_LOG"].strip().lower()
            self.data["logging"]["request_log"] = val in {"1", "true", "yes"}

        # Transport override (http or stdio)
        if "TTYGEIST_TRANSPORT" in os.environ:
            val = os.environ["TTYGEIST_TRANSPORT"].strip().lower()
            if val in {"http", "stdio"}:
                self.data["server"]["transport"] = val

    @property
    def serial_port(self) -> str:
        return self.data["serial"]["port"]

    @property
    def serial_baudrate(self) -> int:
        return self.data["serial"]["baudrate"]

    @property
    def serial_bytesize(self) -> int:
        return self.data["serial"]["bytesize"]

    @property
    def serial_parity(self) -> str:
        return self.data["serial"]["parity"]

    @property
    def serial_stopbits(self) -> float:
        return self.data["serial"]["stopbits"]

    @property
    def serial_timeout(self) -> float:
        return self.data["serial"]["timeout"]

    @property
    def serial_write_timeout(self) -> float:
        return self.data["serial"]["write_timeout"]

    @property
    def serial_dtr(self) -> bool:
        return self.data["serial"].get("dtr", True)

    @property
    def serial_rts(self) -> bool:
        return self.data["serial"].get("rts", False)

    @property
    def reconnect_delay(self) -> float:
        return self.data["serial"]["reconnect_delay"]

    @property
    def max_reconnect_delay(self) -> float:
        return self.data["serial"]["max_reconnect_delay"]

    @property
    def reconnect_backoff_multiplier(self) -> float:
        return self.data["serial"]["reconnect_backoff_multiplier"]

    @property
    def buffer_max_size_bytes(self) -> int:
        return self.data["buffer"]["max_size_mb"] * 1024 * 1024

    @property
    def buffer_line_limit(self) -> int:
        return self.data["buffer"]["line_limit"]

    @property
    def server_host(self) -> str:
        return self.data["server"]["host"]

    @property
    def server_port(self) -> int:
        return self.data["server"]["port"]

    @property
    def server_transport(self) -> str:
        return self.data["server"].get("transport", "stdio")

    @property
    def tls_cert_path(self) -> str:
        return self.data["server"]["tls_cert"]

    @property
    def tls_key_path(self) -> str:
        return self.data["server"]["tls_key"]

    @property
    def log_file(self) -> str:
        return self.data["logging"]["file"]

    @property
    def log_level(self) -> str:
        return self.data["logging"]["level"]

    @property
    def log_format(self) -> str:
        return self.data["logging"]["format"]

    @property
    def log_max_size_bytes(self) -> int:
        return self.data["logging"]["max_size_mb"] * 1024 * 1024

    @property
    def log_backup_count(self) -> int:
        return self.data["logging"]["backup_count"]

    @property
    def api_keys(self) -> list[str]:
        return self.data["auth"]["api_keys"]

    @property
    def allow_anon(self) -> bool:
        return self.data["auth"].get("allow_anon", False)

    @property
    def request_log_enabled(self) -> bool:
        return self.data["logging"].get("request_log", False)

    @property
    def auth_header_name(self) -> str:
        return self.data["auth"]["header_name"]

    @property
    def socket_path(self) -> str:
        """Get socket path with PID expansion."""
        path = self.data["socket"]["path"]
        # Expand ~ and {pid}
        path = os.path.expanduser(path)
        path = path.replace("{pid}", str(os.getpid()))
        return path
