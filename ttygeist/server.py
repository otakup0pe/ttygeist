"""ttygeist - Main server implementation."""

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import time
from typing import Optional

from fastmcp import FastMCP

from ttygeist.config import Config
from ttygeist.buffer_manager import BufferManager
from ttygeist.serial_manager import SerialManager
from ttygeist.auth import APIKeyAuthMiddleware
from ttygeist.socket_server import SocketServer


# Global instances
config: Optional[Config] = None
buffer_manager: Optional[BufferManager] = None
serial_manager: Optional[SerialManager] = None
socket_server: Optional[SocketServer] = None


def setup_logging(config: Config):
    """Configure logging with file handler."""
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, config.log_level.upper()))

    # File handler with rotation
    file_handler = RotatingFileHandler(
        config.log_file,
        maxBytes=config.log_max_size_bytes,
        backupCount=config.log_backup_count
    )
    file_handler.setLevel(getattr(logging, config.log_level.upper()))

    # Formatter
    formatter = logging.Formatter(config.log_format)
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    logging.info("Logging configured")


def create_mcp_server(cfg: Config) -> FastMCP:
    """Create and configure the MCP server."""
    global config, buffer_manager, serial_manager, socket_server

    config = cfg

    # Initialize buffer manager
    buffer_manager = BufferManager(
        max_size_bytes=config.buffer_max_size_bytes,
        line_limit=config.buffer_line_limit
    )

    # Initialize serial manager
    serial_manager = SerialManager(
        port=config.serial_port,
        baudrate=config.serial_baudrate,
        bytesize=config.serial_bytesize,
        parity=config.serial_parity,
        stopbits=config.serial_stopbits,
        timeout=config.serial_timeout,
        write_timeout=config.serial_write_timeout,
        reconnect_delay=config.reconnect_delay,
        max_reconnect_delay=config.max_reconnect_delay,
        reconnect_backoff_multiplier=config.reconnect_backoff_multiplier,
        buffer_manager=buffer_manager
    )

    # Start serial manager
    serial_manager.start()

    # Initialize and start socket server
    socket_server = SocketServer(
        socket_path=config.socket_path,
        buffer_manager=buffer_manager,
        serial_manager=serial_manager
    )
    socket_server.start()

    # Create FastMCP server
    mcp = FastMCP(config.data['server']['name'])

    logging.info("MCP server instance created; registering tools")

    # Register tools in specified order

    @mcp.tool()
    async def serial_read(
        lines: Optional[int] = None,
        duration_seconds: Optional[float] = None,
        clear_after_read: bool = False
    ) -> dict:
        """
        Read data from the serial buffer.

        Args:
            lines: Number of lines to read (None = all available)
            duration_seconds: Read data received within last N seconds (overrides lines)
            clear_after_read: Clear buffer after reading

        Returns:
            Dictionary with read data and statistics
        """
        try:
            logging.info(f"serial_read called: lines={lines}, duration_seconds={duration_seconds}, clear_after_read={clear_after_read}")

            if duration_seconds is not None:
                # Read lines from specific time period
                cutoff_time = time.time() - duration_seconds
                logging.debug(f"Reading all lines and filtering by cutoff_time={cutoff_time}")
                all_lines = await asyncio.wait_for(
                    asyncio.to_thread(buffer_manager.read, lines=None, clear_after_read=False),
                    timeout=10.0
                )
                filtered_lines = [
                    line for line in all_lines
                    if line.timestamp >= cutoff_time
                ]

                if clear_after_read:
                    logging.debug("Clearing buffer after read")
                    await asyncio.wait_for(
                        asyncio.to_thread(buffer_manager.clear),
                        timeout=2.0
                    )

                result = {
                    'success': True,
                    'lines_read': len(filtered_lines),
                    'duration_seconds': duration_seconds,
                    'data': [line.to_dict() for line in filtered_lines]
                }
                logging.info(f"serial_read completed: {len(filtered_lines)} lines")
                return result
            else:
                # Read specific number of lines or all
                logging.debug(f"Reading {lines if lines else 'all'} lines")
                read_lines = await asyncio.wait_for(
                    asyncio.to_thread(
                        buffer_manager.read,
                        lines=lines,
                        clear_after_read=clear_after_read
                    ),
                    timeout=10.0
                )

                result = {
                    'success': True,
                    'lines_read': len(read_lines),
                    'data': [line.to_dict() for line in read_lines]
                }
                logging.info(f"serial_read completed: {len(read_lines)} lines")
                return result
        except asyncio.TimeoutError:
            logging.error("serial_read timeout - buffer manager hung")
            return {
                'success': False,
                'error': 'Operation timed out - buffer manager may be deadlocked',
                'lines_read': 0,
                'data': []
            }
        except Exception as e:
            logging.error(f"serial_read error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'lines_read': 0,
                'data': []
            }

    @mcp.tool()
    async def serial_status() -> dict:
        """
        Get current serial port connection status and statistics.

        Returns:
            Dictionary with connection status, configuration, and statistics
        """
        try:
            logging.info("serial_status called")
            # Add timeout to prevent hanging
            serial_status = await asyncio.wait_for(
                asyncio.to_thread(serial_manager.get_status),
                timeout=5.0
            )
            buffer_stats = await asyncio.wait_for(
                asyncio.to_thread(buffer_manager.get_stats),
                timeout=2.0
            )

            result = {
                'serial': serial_status,
                'buffer': buffer_stats
            }
            logging.info("serial_status completed")
            return result
        except asyncio.TimeoutError:
            logging.error("serial_status timeout - serial manager hung")
            return {
                'success': False,
                'error': 'Operation timed out - serial manager may be deadlocked'
            }
        except Exception as e:
            logging.error(f"serial_status error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    @mcp.tool()
    async def buffer_clear() -> dict:
        """
        Clear all buffered serial data.

        Returns:
            Dictionary with number of lines cleared
        """
        try:
            logging.info("buffer_clear called")
            lines_cleared = await asyncio.wait_for(
                asyncio.to_thread(buffer_manager.clear),
                timeout=5.0
            )

            result = {
                'success': True,
                'lines_cleared': lines_cleared
            }
            logging.info(f"buffer_clear completed: {lines_cleared} lines")
            return result
        except asyncio.TimeoutError:
            logging.error("buffer_clear timeout - buffer manager hung")
            return {
                'success': False,
                'error': 'Operation timed out - buffer manager may be deadlocked',
                'lines_cleared': 0
            }
        except Exception as e:
            logging.error(f"buffer_clear error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'lines_cleared': 0
            }

    @mcp.tool()
    async def buffer_inspect(tail_lines: int = 50) -> dict:
        """
        Inspect buffer contents without consuming data.

        Args:
            tail_lines: Number of recent lines to show (default: 50)

        Returns:
            Dictionary with buffer statistics and recent lines
        """
        try:
            logging.info(f"buffer_inspect called: tail_lines={tail_lines}")
            stats = await asyncio.wait_for(
                asyncio.to_thread(buffer_manager.get_stats),
                timeout=2.0
            )
            recent_lines = await asyncio.wait_for(
                asyncio.to_thread(buffer_manager.read_tail, lines=tail_lines),
                timeout=5.0
            )

            result = {
                'success': True,
                'statistics': stats,
                'tail_lines': len(recent_lines),
                'data': [line.to_dict() for line in recent_lines]
            }
            logging.info(f"buffer_inspect completed: {len(recent_lines)} lines")
            return result
        except asyncio.TimeoutError:
            logging.error("buffer_inspect timeout - buffer manager hung")
            return {
                'success': False,
                'error': 'Operation timed out - buffer manager may be deadlocked',
                'tail_lines': 0,
                'data': []
            }
        except Exception as e:
            logging.error(f"buffer_inspect error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'tail_lines': 0,
                'data': []
            }

    @mcp.tool()
    async def serial_reconnect() -> dict:
        """
        Force a serial port reconnection attempt.

        Returns:
            Dictionary with reconnection result
        """
        try:
            logging.info("serial_reconnect called")
            result = await asyncio.wait_for(
                asyncio.to_thread(serial_manager.reconnect),
                timeout=10.0
            )

            response = {
                'success': result['success'],
                'connected': result['connected'],
                'error': result.get('error')
            }
            logging.info(f"serial_reconnect completed: success={result['success']}, connected={result['connected']}")
            return response
        except asyncio.TimeoutError:
            logging.error("serial_reconnect timeout - serial manager hung")
            return {
                'success': False,
                'connected': False,
                'error': 'Operation timed out - serial manager may be deadlocked'
            }
        except Exception as e:
            logging.error(f"serial_reconnect error: {e}", exc_info=True)
            return {
                'success': False,
                'connected': False,
                'error': str(e)
            }

    @mcp.tool()
    async def serial_write(data: str, add_newline: bool = True) -> dict:
        """
        Write data to the serial port.

        Args:
            data: Data to write to serial port
            add_newline: Append newline character (default: true)

        Returns:
            Dictionary with write status and bytes written
        """
        logging.debug(f"serial_write entry data_len={len(data)}, add_newline={add_newline}")
        try:
            logging.info(f"serial_write called: data_len={len(data)}, add_newline={add_newline}")
            result = await asyncio.wait_for(
                asyncio.to_thread(serial_manager.write, data=data, add_newline=add_newline),
                timeout=5.0
            )

            # Get current status for context
            status = await asyncio.wait_for(
                asyncio.to_thread(serial_manager.get_status),
                timeout=2.0
            )

            response = {
                'success': result['success'],
                'bytes_written': result['bytes_written'],
                'error': result.get('error'),
                'port_connected': status['connected']
            }
            logging.info(f"serial_write completed: success={result['success']}, bytes={result['bytes_written']}")
            return response
        except asyncio.TimeoutError:
            logging.error("serial_write timeout - serial manager hung")
            return {
                'success': False,
                'bytes_written': 0,
                'error': 'Operation timed out - serial manager may be deadlocked',
                'port_connected': False
            }
        except Exception as e:
            logging.error(f"serial_write error: {e}", exc_info=True)
            return {
                'success': False,
                'bytes_written': 0,
                'error': str(e),
                'port_connected': False
            }

    logging.info("Tools registered: serial_read, serial_status, buffer_clear, buffer_inspect, serial_reconnect, serial_write")

    return mcp


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='ttygeist')
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--transport',
        choices=['http', 'stdio'],
        help='Override transport (http or stdio)'
    )
    args = parser.parse_args()

    # Load configuration
    cfg = Config(config_path=args.config)

    # Setup logging
    setup_logging(cfg)

    logging.info("=" * 60)
    logging.info("Starting ttygeist")
    logging.info(f"Serial Port: {cfg.serial_port}")
    logging.info(f"Baud Rate: {cfg.serial_baudrate}")
    logging.info(f"Transport: {cfg.server_transport}")
    if cfg.server_transport == 'http':
        logging.info(f"Server: https://{cfg.server_host}:{cfg.server_port}")
    logging.info("=" * 60)

    # Resolve transport preference (CLI > env > config already applied for env)
    transport_override = args.transport
    transport = transport_override or cfg.server_transport
    transport = transport.lower()

    # Validate API keys / anon mode (HTTP only)
    if transport == 'http':
        if not cfg.api_keys and not cfg.allow_anon:
            logging.error("No API keys configured and anonymous access disabled. Server will not start.")
            logging.error("Add keys to config file or set TTYGEIST_API_KEYS, or enable allow_anon for local dev.")
            return
        if not cfg.api_keys and cfg.allow_anon:
            logging.warning("Starting in anonymous mode (no auth middleware). Do NOT use in production.")
        else:
            logging.info(f"Configured with {len(cfg.api_keys)} API key(s)")
    else:
        logging.info("STDIO transport selected; HTTP auth settings ignored")

    # Create MCP server
    mcp = create_mcp_server(cfg)

    if transport == 'stdio':
        logging.info("Starting ttygeist via stdio transport")
        try:
            mcp.run()
        except KeyboardInterrupt:
            logging.info("Received shutdown signal (stdio)")
        finally:
            if socket_server:
                socket_server.stop()
            if serial_manager:
                serial_manager.stop()
            logging.info("ttygeist Server stopped")
        return

    # HTTP path remains unchanged
    mcp_app = mcp.http_app(path='/')

    # Wrap with auth middleware unless anonymous mode.
    asgi_app = mcp_app
    if cfg.api_keys and not cfg.allow_anon:
        from ttygeist.auth import APIKeyAuthASGIMiddleware
        asgi_app = APIKeyAuthASGIMiddleware(
            asgi_app,
            api_keys=cfg.api_keys,
            header_name=cfg.auth_header_name,
        )
        logging.info(f"Auth middleware enabled using header '{cfg.auth_header_name}'")
    else:
        logging.info("Auth middleware disabled")

    # Optional lightweight request logging
    if cfg.request_log_enabled and cfg.log_level.upper() == 'DEBUG':
        async def request_logger(scope, receive, send):
            if scope.get('type') == 'http':
                logging.debug(f"HTTP {scope.get('method')} {scope.get('path')}")
            return await asgi_app(scope, receive, send)
        asgi_app = request_logger
        logging.debug("Request logging middleware active")

    from starlette.applications import Starlette
    from starlette.routing import Mount

    app = Starlette(
        routes=[
            Mount('/', app=asgi_app),
        ],
        lifespan=mcp_app.lifespan,
    )

    logging.info("HTTP server application assembled and mounted at /")

    try:
        import uvicorn
        uvicorn.run(
            app,
            host=cfg.server_host,
            port=cfg.server_port,
            ssl_certfile=cfg.tls_cert_path,
            ssl_keyfile=cfg.tls_key_path,
            log_level=cfg.log_level.lower()
        )
    except KeyboardInterrupt:
        logging.info("Received shutdown signal")
    finally:
        if socket_server:
            socket_server.stop()
        if serial_manager:
            serial_manager.stop()
        logging.info("ttygeist Server stopped")


if __name__ == '__main__':
    main()
