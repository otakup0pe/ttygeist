"""Tests for Config."""

import os

from ttygeist.config import Config


class TestYAMLLoading:
    def test_load_from_file(self, tmp_config_file, clean_env):
        path = tmp_config_file(
            "serial:\n  port: /dev/ttyACM0\n  baudrate: 9600\n"
            "buffer:\n  max_size_mb: 5\n  line_limit: 500\n  overflow: fifo\n"
            "server:\n  host: 0.0.0.0\n  port: 9000\n  tls_cert: c.pem\n  tls_key: k.pem\n  name: test\n  transport: http\n"
            "logging:\n  file: test.log\n  level: DEBUG\n  format: '%(message)s'\n  max_size_mb: 10\n  backup_count: 1\n  request_log: true\n"
            "auth:\n  api_keys:\n    - key1\n  header_name: X-Token\n  allow_anon: false\n"
            "socket:\n  path: /tmp/test-{pid}.sock\n"
        )
        cfg = Config(config_path=path)
        assert cfg.serial_port == "/dev/ttyACM0"
        assert cfg.serial_baudrate == 9600
        assert cfg.buffer_max_size_bytes == 5 * 1024 * 1024
        assert cfg.buffer_line_limit == 500
        assert cfg.server_host == "0.0.0.0"
        assert cfg.server_port == 9000
        assert cfg.log_level == "DEBUG"
        assert cfg.api_keys == ["key1"]
        assert cfg.auth_header_name == "X-Token"
        assert cfg.request_log_enabled is True

    def test_missing_file_uses_defaults(self, clean_env):
        cfg = Config(config_path="/nonexistent/path.yaml")
        assert cfg.serial_port == "/dev/ttyUSB0"

    def test_tls_paths(self, tmp_config_file, clean_env):
        path = tmp_config_file(
            "serial:\n  port: x\n  baudrate: 9600\n  bytesize: 8\n  parity: N\n  stopbits: 1\n  timeout: 1\n  write_timeout: 1\n  reconnect_delay: 2\n  max_reconnect_delay: 30\n  reconnect_backoff_multiplier: 1.5\n"
            "buffer:\n  max_size_mb: 1\n  line_limit: 10\n  overflow: fifo\n"
            "server:\n  host: x\n  port: 1\n  tls_cert: /etc/cert.pem\n  tls_key: /etc/key.pem\n  name: x\n  transport: http\n"
            "logging:\n  file: x\n  level: INFO\n  format: x\n  max_size_mb: 1\n  backup_count: 1\n  request_log: false\n"
            "auth:\n  api_keys: []\n  header_name: X-API-Key\n  allow_anon: false\n"
            "socket:\n  path: /tmp/t.sock\n"
        )
        cfg = Config(config_path=path)
        assert cfg.tls_cert_path == "/etc/cert.pem"
        assert cfg.tls_key_path == "/etc/key.pem"


class TestEnvOverrides:
    def test_api_keys_from_env(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_API_KEYS", "abc,def")
        cfg = Config()
        assert "abc" in cfg.api_keys
        assert "def" in cfg.api_keys

    def test_api_keys_strips_whitespace(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_API_KEYS", " key1 , key2 ")
        cfg = Config()
        assert cfg.api_keys == ["key1", "key2"]

    def test_api_keys_extend_existing(self, tmp_config_file, monkeypatch, clean_env):
        path = tmp_config_file(
            "serial:\n  port: x\n  baudrate: 9600\n  bytesize: 8\n  parity: N\n  stopbits: 1\n  timeout: 1\n  write_timeout: 1\n  reconnect_delay: 2\n  max_reconnect_delay: 30\n  reconnect_backoff_multiplier: 1.5\n"
            "buffer:\n  max_size_mb: 1\n  line_limit: 10\n  overflow: fifo\n"
            "server:\n  host: x\n  port: 1\n  tls_cert: x\n  tls_key: x\n  name: x\n  transport: http\n"
            "logging:\n  file: x\n  level: INFO\n  format: x\n  max_size_mb: 1\n  backup_count: 1\n  request_log: false\n"
            "auth:\n  api_keys:\n    - fromfile\n  header_name: X-API-Key\n  allow_anon: false\n"
            "socket:\n  path: /tmp/t.sock\n"
        )
        monkeypatch.setenv("TTYGEIST_API_KEYS", "fromenv")
        cfg = Config(config_path=path)
        assert "fromfile" in cfg.api_keys
        assert "fromenv" in cfg.api_keys

    def test_serial_port_override(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_PORT", "/dev/ttyACM1")
        cfg = Config()
        assert cfg.serial_port == "/dev/ttyACM1"

    def test_auth_header_override(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_AUTH_HEADER", "Authorization")
        cfg = Config()
        assert cfg.auth_header_name == "Authorization"

    def test_allow_anon_true_variants(self, monkeypatch, clean_env):
        for val in ("1", "true", "yes", "TRUE", "Yes"):
            monkeypatch.setenv("TTYGEIST_ALLOW_ANON", val)
            cfg = Config()
            assert cfg.allow_anon is True, f"Failed for {val!r}"

    def test_allow_anon_false_variants(self, monkeypatch, clean_env):
        for val in ("0", "false", "no", "anything"):
            monkeypatch.setenv("TTYGEIST_ALLOW_ANON", val)
            cfg = Config()
            assert cfg.allow_anon is False, f"Failed for {val!r}"

    def test_request_log_override(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_REQUEST_LOG", "true")
        cfg = Config()
        assert cfg.request_log_enabled is True

    def test_transport_override_http(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_TRANSPORT", "http")
        cfg = Config()
        assert cfg.server_transport == "http"

    def test_transport_override_stdio(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_TRANSPORT", "stdio")
        cfg = Config()
        assert cfg.server_transport == "stdio"

    def test_transport_invalid_ignored(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_TRANSPORT", "grpc")
        cfg = Config()
        assert cfg.server_transport == "stdio"


class TestSocketPath:
    def test_pid_expansion(self, clean_env):
        cfg = Config()
        path = cfg.socket_path
        assert str(os.getpid()) in path
        assert "{pid}" not in path

    def test_tilde_expansion(self, clean_env):
        cfg = Config()
        path = cfg.socket_path
        assert "~" not in path
