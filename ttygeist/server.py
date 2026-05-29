"""ttygeist - Multi-device MCP server for serial port communication."""

import argparse
import asyncio
import atexit
import contextlib
import logging
import re
import signal
import sys
import time
from logging.handlers import RotatingFileHandler

from fastmcp import FastMCP

from ttygeist.config import Config, load_config
from ttygeist.device_registry import (
    AmbiguousDeviceError,
    DeviceNotFoundError,
    DeviceRegistry,
)
from ttygeist.socket_server import SocketServer

# Global instances
registry: DeviceRegistry | None = None
socket_server: SocketServer | None = None
_shutdown_executed: bool = False

# Regex for Python-style escape sequences from JSON-RPC
_ESCAPE_RE = re.compile(
    r"\\x([0-9a-fA-F]{2})"
    r"|\\u([0-9a-fA-F]{4})"
    r"|\\([nrtab0\\])"
)
_SIMPLE_ESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "a": "\a",
    "b": "\b",
    "0": "\0",
    "\\": "\\",
}


def _decode_escapes(data: str) -> str:
    """Decode Python-style escape sequences in a string from JSON-RPC."""

    def _replace(m):
        if m.group(1) is not None:
            return chr(int(m.group(1), 16))
        if m.group(2) is not None:
            return chr(int(m.group(2), 16))
        return _SIMPLE_ESCAPES[m.group(3)]

    return _ESCAPE_RE.sub(_replace, data)


def setup_logging(config: Config):
    """Configure logging. Prefers journald, falls back to file."""
    logger = logging.getLogger()
    level = getattr(logging, config.log_level.upper())
    logger.setLevel(level)

    handler = None

    if config.log_target == "journal":
        try:
            from systemd.journal import JournalHandler

            handler = JournalHandler(SYSLOG_IDENTIFIER="ttygeist")
            handler.setLevel(level)
        except ImportError:
            # python-systemd not available, fall back to file
            pass

    if handler is None:
        # File handler (default fallback or explicit target=file)
        handler = RotatingFileHandler(
            config.log_file,
            maxBytes=config.log_max_size_bytes,
            backupCount=config.log_backup_count,
        )
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(config.log_format))

    logger.addHandler(handler)
    logging.info("Logging configured (target: %s)", config.log_target)


def graceful_shutdown(source: str = "unknown"):
    """Idempotent shutdown for all subsystems."""
    global _shutdown_executed
    if _shutdown_executed:
        return
    _shutdown_executed = True
    logging.info("Graceful shutdown initiated by %s", source)
    try:
        if socket_server:
            with contextlib.suppress(Exception):
                socket_server.stop()
        if registry:
            registry.stop_background_scan()
            registry.stop_firmware_monitor()
            registry.disconnect_all()
    finally:
        logging.info("ttygeist stopped")


def _resolve_device(device: str | None):
    """Resolve device name to entry."""
    return registry.resolve(device)


def create_mcp_server(cfg: Config) -> FastMCP:
    """Create and configure the MCP server."""
    global registry, socket_server

    registry = DeviceRegistry(cfg)

    # Initial scan + connect
    scan_result = registry.connect_all()
    logging.info("Initial scan: %s", scan_result)

    # Start background threads
    registry.start_background_scan()
    registry.start_firmware_monitor()

    # Socket server -- resolves device via registry per-request (hot-plug safe)
    socket_server = SocketServer(
        socket_path=cfg.socket_path_expanded,
        device_registry=registry,
    )
    socket_server.start()

    mcp = FastMCP(cfg.server_name)
    logging.info("MCP server created; registering tools")

    # Signal handlers
    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        graceful_shutdown(f"signal {sig_name}")
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        with contextlib.suppress(Exception):
            signal.signal(sig, _signal_handler)

    atexit.register(lambda: graceful_shutdown("atexit"))

    # ---- Tool registration (respects disabled_tools config) ----

    def _register(tool_func, name: str | None = None):
        """Register a tool if not disabled."""
        tool_name = name or tool_func.__name__
        if cfg.is_tool_enabled(tool_name):
            mcp.tool()(tool_func)
        else:
            logging.info("Tool '%s' disabled by config", tool_name)

    # -- serial_status: single device or fleet overview --

    async def serial_status(device: str | None = None) -> dict:
        """Get serial port status.

        With no device arg (or multiple devices), returns status for
        all managed devices. With a device name, returns detailed
        status for that device.

        Args:
            device: Device name. Omit for fleet overview.
        """
        try:
            if device is None and len(registry.devices) != 1:
                return {"success": True, **registry.status()}

            entry = _resolve_device(device)
            sm = entry.serial_manager
            buf = entry.buffer_manager

            serial_info = (
                await asyncio.wait_for(asyncio.to_thread(sm.get_status), timeout=5.0) if sm else {"connected": False}
            )

            buffer_info = await asyncio.wait_for(asyncio.to_thread(buf.get_stats), timeout=2.0) if buf else {}

            result = {
                "success": True,
                "device": entry.name,
                "serial": serial_info,
                "buffer": buffer_info,
            }
            if entry.usb_info:
                result["usb"] = entry.usb_info.to_dict()
            if entry.firmware_info and entry.firmware_info.product:
                result["firmware"] = entry.firmware_info.to_dict()
            return result
        except (DeviceNotFoundError, AmbiguousDeviceError) as e:
            return {"success": False, "error": str(e)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "Operation timed out"}
        except Exception as e:
            logging.error("serial_status error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    _register(serial_status)

    # -- serial_read --

    async def serial_read(
        device: str | None = None,
        lines: int | None = None,
        duration_seconds: float | None = None,
        clear_after_read: bool = False,
    ) -> dict:
        """Read data from a device's serial buffer.

        Args:
            device: Device name. Optional when only one device exists.
            lines: Number of lines to read (None = all available).
            duration_seconds: Read data received within last N seconds.
            clear_after_read: Clear buffer after reading.
        """
        try:
            entry = _resolve_device(device)
            buf = entry.buffer_manager
            if buf is None:
                return {"success": False, "error": f"Device '{entry.name}' has no buffer"}

            if duration_seconds is not None:
                cutoff_time = time.time() - duration_seconds
                all_lines = await asyncio.wait_for(
                    asyncio.to_thread(buf.read, lines=None, clear_after_read=False),
                    timeout=10.0,
                )
                filtered = [line for line in all_lines if line.timestamp >= cutoff_time]
                if clear_after_read:
                    await asyncio.wait_for(asyncio.to_thread(buf.clear), timeout=2.0)
                return {
                    "success": True,
                    "device": entry.name,
                    "lines_read": len(filtered),
                    "data": [line.to_dict() for line in filtered],
                }
            else:
                read_lines = await asyncio.wait_for(
                    asyncio.to_thread(buf.read, lines=lines, clear_after_read=clear_after_read),
                    timeout=10.0,
                )
                return {
                    "success": True,
                    "device": entry.name,
                    "lines_read": len(read_lines),
                    "data": [line.to_dict() for line in read_lines],
                }
        except (DeviceNotFoundError, AmbiguousDeviceError) as e:
            return {"success": False, "error": str(e)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "Operation timed out"}
        except Exception as e:
            logging.error("serial_read error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    _register(serial_read)

    # -- serial_write --

    async def serial_write(data: str, device: str | None = None, add_newline: bool = True) -> dict:
        """Write data to a device's serial port.

        Args:
            data: Data to write. Escape sequences (\\x03, \\n) are decoded.
            device: Device name. Optional when only one device exists.
            add_newline: Append newline character (default: true).
        """
        try:
            entry = _resolve_device(device)
            sm = entry.serial_manager
            if sm is None:
                return {"success": False, "error": f"Device '{entry.name}' not connected"}

            decoded_data = _decode_escapes(data)
            result = await asyncio.wait_for(
                asyncio.to_thread(sm.write, data=decoded_data, add_newline=add_newline),
                timeout=5.0,
            )
            return {
                "success": result["success"],
                "device": entry.name,
                "bytes_written": result["bytes_written"],
                "error": result.get("error"),
            }
        except (DeviceNotFoundError, AmbiguousDeviceError) as e:
            return {"success": False, "error": str(e)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "Operation timed out"}
        except Exception as e:
            logging.error("serial_write error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    _register(serial_write)

    # -- buffer_inspect --

    async def buffer_inspect(device: str | None = None, tail_lines: int = 50) -> dict:
        """Inspect buffer contents without consuming data.

        Args:
            device: Device name. Optional when only one device exists.
            tail_lines: Number of recent lines to show (default: 50).
        """
        try:
            entry = _resolve_device(device)
            buf = entry.buffer_manager
            if buf is None:
                return {"success": False, "error": f"Device '{entry.name}' has no buffer"}

            stats = await asyncio.wait_for(asyncio.to_thread(buf.get_stats), timeout=2.0)
            recent = await asyncio.wait_for(asyncio.to_thread(buf.read_tail, lines=tail_lines), timeout=5.0)

            return {
                "success": True,
                "device": entry.name,
                "statistics": stats,
                "tail_lines": len(recent),
                "data": [line.to_dict() for line in recent],
            }
        except (DeviceNotFoundError, AmbiguousDeviceError) as e:
            return {"success": False, "error": str(e)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "Operation timed out"}
        except Exception as e:
            logging.error("buffer_inspect error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    _register(buffer_inspect)

    # -- buffer_clear --

    async def buffer_clear(device: str | None = None) -> dict:
        """Clear all buffered serial data for a device.

        Args:
            device: Device name. Optional when only one device exists.
        """
        try:
            entry = _resolve_device(device)
            buf = entry.buffer_manager
            if buf is None:
                return {"success": False, "error": f"Device '{entry.name}' has no buffer"}

            lines_cleared = await asyncio.wait_for(asyncio.to_thread(buf.clear), timeout=5.0)
            return {"success": True, "device": entry.name, "lines_cleared": lines_cleared}
        except (DeviceNotFoundError, AmbiguousDeviceError) as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logging.error("buffer_clear error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    _register(buffer_clear)

    # -- serial_control --

    async def serial_control(action: str, device: str | None = None) -> dict:
        """Control a device's serial port connection.

        Args:
            action: "reconnect", "suspend", or "resume".
                suspend releases the port for external tools (esptool).
                resume re-acquires after external tool use. Background
                scan will pick up any port path changes automatically.
            device: Device name. Optional when only one device exists.
        """
        valid_actions = ("reconnect", "suspend", "resume")
        if action not in valid_actions:
            return {"success": False, "error": f"Unknown action '{action}'. Valid: {', '.join(valid_actions)}"}

        try:
            entry = _resolve_device(device)
            sm = entry.serial_manager
            if sm is None:
                return {"success": False, "error": f"Device '{entry.name}' not connected"}

            if action == "suspend":
                result = await asyncio.wait_for(asyncio.to_thread(sm.suspend), timeout=10.0)
            elif action == "resume":
                result = await asyncio.wait_for(asyncio.to_thread(sm.resume), timeout=10.0)
            else:
                result = await asyncio.wait_for(asyncio.to_thread(sm.reconnect), timeout=10.0)

            return {
                "success": result["success"],
                "device": entry.name,
                "action": action,
                "connected": result.get("connected", False),
                "error": result.get("error"),
            }
        except (DeviceNotFoundError, AmbiguousDeviceError) as e:
            return {"success": False, "error": str(e)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "Operation timed out"}
        except Exception as e:
            logging.error("serial_control error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    _register(serial_control)

    # -- server_shutdown --

    async def server_shutdown() -> dict:
        """Cooperative shutdown request."""
        logging.info("server_shutdown tool invoked")
        graceful_shutdown("tool server_shutdown")
        asyncio.get_running_loop().call_soon(sys.exit, 0)
        return {"success": True}

    _register(server_shutdown)

    # Log registered tools
    registered = [
        t
        for t in [
            "serial_status",
            "serial_read",
            "serial_write",
            "buffer_inspect",
            "buffer_clear",
            "serial_control",
            "server_shutdown",
        ]
        if cfg.is_tool_enabled(t)
    ]
    logging.info("Tools registered: %s", ", ".join(registered))

    return mcp


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="ttygeist - multi-device serial MCP server")
    parser.add_argument("--config", default="config.yaml", help="Path to configuration file")
    parser.add_argument("--transport", choices=["http", "stdio"], help="Override transport")
    args = parser.parse_args()

    cfg = load_config(config_path=args.config)
    setup_logging(cfg)

    logging.info("=" * 60)
    logging.info("Starting ttygeist")
    logging.info("Named devices: %d", len(cfg.devices))
    logging.info("Block list: %s", cfg.block)
    logging.info("Disabled tools: %s", cfg.disabled_tools or "(none)")
    logging.info("Transport: %s", cfg.server_transport)
    for name, dev in cfg.devices.items():
        if dev.serial:
            logging.info("  %s: serial=%s baud=%d", name, dev.serial, dev.baud)
        elif dev.port:
            logging.info("  %s: port=%s baud=%d", name, dev.port, dev.baud)
    logging.info("=" * 60)

    transport = args.transport or cfg.server_transport
    transport = transport.lower()

    if transport == "http":
        if not cfg.api_keys and not cfg.allow_anon:
            logging.error("No API keys and anonymous access disabled.")
            return
        if not cfg.api_keys and cfg.allow_anon:
            logging.warning("Anonymous mode enabled.")

    mcp = create_mcp_server(cfg)

    if transport == "stdio":
        logging.info("Starting ttygeist via stdio transport")
        try:
            mcp.run()
        except KeyboardInterrupt:
            pass
        finally:
            graceful_shutdown("stdio exit")
        return

    # HTTP path
    mcp_app = mcp.http_app(path="/")
    asgi_app = mcp_app

    if cfg.api_keys and not cfg.allow_anon:
        from ttygeist.auth import APIKeyAuthASGIMiddleware

        asgi_app = APIKeyAuthASGIMiddleware(
            asgi_app,
            api_keys=cfg.api_keys,
            header_name=cfg.auth_header_name,
        )

    if cfg.request_log_enabled and cfg.log_level.upper() == "DEBUG":

        async def request_logger(scope, receive, send):
            if scope.get("type") == "http":
                logging.debug("HTTP %s %s", scope.get("method"), scope.get("path"))
            return await asgi_app(scope, receive, send)

        asgi_app = request_logger

    from starlette.applications import Starlette
    from starlette.routing import Mount

    app = Starlette(routes=[Mount("/", app=asgi_app)], lifespan=mcp_app.lifespan)

    try:
        import uvicorn

        uvicorn.run(
            app,
            host=cfg.server_host,
            port=cfg.server_port,
            ssl_certfile=cfg.tls_cert_path,
            ssl_keyfile=cfg.tls_key_path,
            log_level=cfg.log_level.lower(),
        )
    except KeyboardInterrupt:
        pass
    finally:
        graceful_shutdown("http exit")


if __name__ == "__main__":
    main()
