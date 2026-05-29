"""Tests for ttygeist config parsing."""

import os

from ttygeist.config import load_config


class TestDeviceParsing:
    def test_short_form(self, tmp_config_file, clean_env):
        path = tmp_config_file("devices:\n  mydev: 'AB:CD:EF:12:34:56'\n")
        cfg = load_config(config_path=path)
        assert "mydev" in cfg.devices
        assert cfg.devices["mydev"].serial == "AB:CD:EF:12:34:56"
        assert cfg.devices["mydev"].baud == 115200  # default

    def test_extended_form(self, tmp_config_file, clean_env):
        path = tmp_config_file("devices:\n  mydev:\n    serial: 'AB:CD:EF:12:34:56'\n    baud: 460800\n")
        cfg = load_config(config_path=path)
        assert cfg.devices["mydev"].serial == "AB:CD:EF:12:34:56"
        assert cfg.devices["mydev"].baud == 460800

    def test_explicit_port(self, tmp_config_file, clean_env):
        path = tmp_config_file("devices:\n  gps:\n    port: /dev/ttyS1\n    baud: 9600\n")
        cfg = load_config(config_path=path)
        assert cfg.devices["gps"].port == "/dev/ttyS1"
        assert cfg.devices["gps"].serial is None
        assert cfg.devices["gps"].baud == 9600

    def test_mixed_forms(self, tmp_config_file, clean_env):
        path = tmp_config_file(
            "devices:\n  dev1: 'AAAA'\n  dev2:\n    serial: 'BBBB'\n    baud: 9600\n  dev3:\n    port: /dev/ttyS0\n"
        )
        cfg = load_config(config_path=path)
        assert len(cfg.devices) == 3
        assert cfg.devices["dev1"].serial == "AAAA"
        assert cfg.devices["dev2"].baud == 9600
        assert cfg.devices["dev3"].port == "/dev/ttyS0"

    def test_no_devices(self, tmp_config_file, clean_env):
        path = tmp_config_file("server:\n  name: test\n")
        cfg = load_config(config_path=path)
        assert cfg.devices == {}

    def test_defaults_applied(self, tmp_config_file, clean_env):
        path = tmp_config_file("defaults:\n  baud: 9600\n  dtr: false\ndevices:\n  mydev: 'SERIAL1'\n")
        cfg = load_config(config_path=path)
        assert cfg.devices["mydev"].baud == 9600
        assert cfg.devices["mydev"].dtr is False

    def test_device_overrides_defaults(self, tmp_config_file, clean_env):
        path = tmp_config_file("defaults:\n  baud: 9600\ndevices:\n  mydev:\n    serial: 'S1'\n    baud: 460800\n")
        cfg = load_config(config_path=path)
        assert cfg.devices["mydev"].baud == 460800


class TestBlockList:
    def test_block_list(self, tmp_config_file, clean_env):
        path = tmp_config_file("block:\n  - 'BLOCKED1'\n  - 'BLOCKED2'\n")
        cfg = load_config(config_path=path)
        assert cfg.block == ["BLOCKED1", "BLOCKED2"]

    def test_empty_block(self, clean_env):
        cfg = load_config()
        assert cfg.block == []


class TestDisabledTools:
    def test_disabled_tools(self, tmp_config_file, clean_env):
        path = tmp_config_file("disabled_tools:\n  - server_shutdown\n  - buffer_clear\n")
        cfg = load_config(config_path=path)
        assert cfg.is_tool_enabled("serial_read") is True
        assert cfg.is_tool_enabled("server_shutdown") is False
        assert cfg.is_tool_enabled("buffer_clear") is False


class TestFirmwarePatterns:
    def test_patterns_loaded(self, tmp_config_file, clean_env):
        path = tmp_config_file(
            "firmware_patterns:\n"
            "  - pattern: 'Version:\\\\s*(.+)'\n"
            "    field: firmware_version\n"
            "  - pattern: 'MyDevice Boot'\n"
            "    product: mydevice\n"
        )
        cfg = load_config(config_path=path)
        assert len(cfg.firmware_patterns) == 2
        assert cfg.firmware_patterns[0]["field"] == "firmware_version"
        assert cfg.firmware_patterns[1]["product"] == "mydevice"

    def test_no_patterns(self, clean_env):
        cfg = load_config()
        assert cfg.firmware_patterns == []


class TestServerConfig:
    def test_server_defaults(self, clean_env):
        cfg = load_config()
        assert cfg.server_name == "ttygeist"
        assert cfg.server_transport == "stdio"

    def test_server_from_yaml(self, tmp_config_file, clean_env):
        path = tmp_config_file("server:\n  name: testghost\n  transport: http\n  host: 0.0.0.0\n  port: 9000\n")
        cfg = load_config(config_path=path)
        assert cfg.server_name == "testghost"
        assert cfg.server_transport == "http"
        assert cfg.server_host == "0.0.0.0"
        assert cfg.server_port == 9000

    def test_tls_paths(self, tmp_config_file, clean_env):
        path = tmp_config_file("server:\n  tls_cert: /etc/cert.pem\n  tls_key: /etc/key.pem\n")
        cfg = load_config(config_path=path)
        assert cfg.tls_cert_path == "/etc/cert.pem"
        assert cfg.tls_key_path == "/etc/key.pem"


class TestBufferConfig:
    def test_defaults(self, clean_env):
        cfg = load_config()
        assert cfg.buffer_max_size_bytes == 10 * 1024 * 1024
        assert cfg.buffer_line_limit == 100000

    def test_from_yaml(self, tmp_config_file, clean_env):
        path = tmp_config_file("buffer:\n  max_size_mb: 5\n  line_limit: 500\n")
        cfg = load_config(config_path=path)
        assert cfg.buffer_max_size_bytes == 5 * 1024 * 1024
        assert cfg.buffer_line_limit == 500


class TestLoggingConfig:
    def test_defaults(self, clean_env):
        cfg = load_config()
        assert cfg.log_target == "journal"
        assert cfg.log_level == "INFO"

    def test_file_target(self, tmp_config_file, clean_env):
        path = tmp_config_file("logging:\n  target: file\n  file: mylog.log\n  level: DEBUG\n")
        cfg = load_config(config_path=path)
        assert cfg.log_target == "file"
        assert cfg.log_file == "mylog.log"
        assert cfg.log_level == "DEBUG"


class TestMissingFile:
    def test_missing_file_uses_defaults(self, clean_env):
        cfg = load_config(config_path="/nonexistent/path.yaml")
        assert cfg.devices == {}
        assert cfg.server_transport == "stdio"

    def test_none_path(self, clean_env):
        cfg = load_config(config_path=None)
        assert cfg.devices == {}


class TestEnvOverrides:
    def test_api_keys_from_env(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_API_KEYS", "abc,def")
        cfg = load_config()
        assert "abc" in cfg.api_keys
        assert "def" in cfg.api_keys

    def test_api_keys_strips_whitespace(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_API_KEYS", " key1 , key2 ")
        cfg = load_config()
        assert cfg.api_keys == ["key1", "key2"]

    def test_auth_header_override(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_AUTH_HEADER", "Authorization")
        cfg = load_config()
        assert cfg.auth_header_name == "Authorization"

    def test_allow_anon_true_variants(self, monkeypatch, clean_env):
        for val in ("1", "true", "yes", "TRUE", "Yes"):
            monkeypatch.setenv("TTYGEIST_ALLOW_ANON", val)
            cfg = load_config()
            assert cfg.allow_anon is True, f"Failed for {val!r}"

    def test_allow_anon_false_variants(self, monkeypatch, clean_env):
        for val in ("0", "false", "no", "anything"):
            monkeypatch.setenv("TTYGEIST_ALLOW_ANON", val)
            cfg = load_config()
            assert cfg.allow_anon is False, f"Failed for {val!r}"

    def test_transport_override(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_TRANSPORT", "http")
        cfg = load_config()
        assert cfg.server_transport == "http"

    def test_transport_invalid_ignored(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_TRANSPORT", "grpc")
        cfg = load_config()
        assert cfg.server_transport == "stdio"

    def test_request_log_override(self, monkeypatch, clean_env):
        monkeypatch.setenv("TTYGEIST_REQUEST_LOG", "true")
        cfg = load_config()
        assert cfg.request_log_enabled is True


class TestSocketPath:
    def test_pid_expansion(self, clean_env):
        cfg = load_config()
        path = cfg.socket_path_expanded
        assert str(os.getpid()) in path
        assert "{pid}" not in path

    def test_tilde_expansion(self, clean_env):
        cfg = load_config()
        path = cfg.socket_path_expanded
        assert "~" not in path
