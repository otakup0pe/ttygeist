"""CLI interface for Serial MCP Server (enhanced interactive terminal)."""

import argparse
import logging
import sys
import termios
import tty
import threading
import time
from pathlib import Path
from typing import Optional

from ttygeist.config import Config
from ttygeist.socket_client import SocketClient
from ttygeist.terminal_emulator import TerminalEmulator, detect_terminal_capabilities


EXIT_KEYS = {
    '\x1d': 'Ctrl+]',  # traditional telnet escape
}
# Allow Ctrl+C and Ctrl+D to exit gracefully (optional)
OPTIONAL_EXIT_KEYS = {
    '\x03': 'Ctrl+C',
    '\x04': 'Ctrl+D',
}


def find_socket_path(config_path: Optional[str] = None) -> str:
    config = Config(config_path)
    socket_pattern = config.data['socket']['path']
    socket_pattern = socket_pattern.replace('~', str(Path.home()))
    if '{pid}' in socket_pattern:
        import glob
        pattern = socket_pattern.replace('{pid}', '*')
        matches = glob.glob(pattern)
        if not matches:
            print(f"Error: No socket found matching pattern: {pattern}",
                  file=sys.stderr)
            print("\nIs the MCP server running? Start it with:",
                  file=sys.stderr)
            print("  uv run ttygeist", file=sys.stderr)
            sys.exit(1)
        elif len(matches) > 1:
            print(f"Error: Multiple sockets found: {matches}",
                  file=sys.stderr)
            print("Please specify which one or stop extra servers.",
                  file=sys.stderr)
            sys.exit(1)
        return matches[0]
    return socket_pattern


def cmd_show(args):
    socket_path = find_socket_path(args.config)
    client = SocketClient(socket_path)
    try:
        client.connect()
        result = client.buffer_inspect(tail_lines=args.lines)
        for line in result["lines"]:
            print(line, end='')
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def cmd_tail(args):
    socket_path = find_socket_path(args.config)
    client = SocketClient(socket_path)
    try:
        client.connect()
        if args.lines > 0:
            result = client.buffer_inspect(tail_lines=args.lines)
            for line in result["lines"]:
                print(line, end='')

        def print_line(line: str):
            print(line, end='', flush=True)

        client.buffer_stream(print_line)
    except KeyboardInterrupt:
        print("\nStopped tailing.", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def cmd_status(args):
    socket_path = find_socket_path(args.config)
    client = SocketClient(socket_path)
    try:
        client.connect()
        status = client.serial_status()
        print("Serial Connection:")
        print(f"  Connected: {status['connected']}")
        print(f"  Port: {status['port']}")
        print(f"  Baudrate: {status['baudrate']}")
        if status['uptime_seconds']:
            print(f"  Uptime: {status['uptime_seconds']}s")
        print(f"  Error count: {status['error_count']}")
        print(f"  Reconnect count: {status['reconnect_count']}")
        if status['last_error']:
            print(f"  Last error: {status['last_error']}")
        print()
        print("Buffer Status:")
        buffer_stats = status['buffer_stats']
        print(f"  Current lines: {buffer_stats['current_lines']}")
        print(f"  Size: {buffer_stats['current_size_mb']} MB")
        print(f"  Max size: {buffer_stats['max_size_mb']} MB")
        print(f"  Line limit: {buffer_stats['line_limit']}")
        print(f"  Utilization: {buffer_stats['utilization_percent']}%")
        print(f"  Total received: {buffer_stats['total_lines_received']}")
        print(f"  Total dropped: {buffer_stats['total_lines_dropped']}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def cmd_terminal(args):
    socket_path = find_socket_path(args.config)
    # Use separate clients for streaming and requests to avoid recv() races
    stream_client = SocketClient(socket_path)
    write_client = SocketClient(socket_path)

    if args.verbose:
        log_file = Path.home() / '.ttygeist' / 'terminal.log'
        log_file.parent.mkdir(exist_ok=True)
        logging.basicConfig(
            filename=str(log_file),
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        print(f"Verbose logging enabled: {log_file}", file=sys.stderr)

    # Predefine for except block
    tty_fd = None
    old_settings = None

    try:
        stream_client.connect()
        write_client.connect()
        color_support, unicode_support = detect_terminal_capabilities()
        if args.verbose:
            print(f"Terminal capabilities: color={color_support},"
                  f" unicode={unicode_support}",
                  file=sys.stderr)
        emulator = TerminalEmulator(color_support=color_support)

        if args.lines > 0:
            result = stream_client.buffer_inspect(tail_lines=args.lines)
            for line in result["lines"]:
                processed = emulator.process_string(line)
                print(processed, end='', flush=True)

        # Banner
        esc_keys = ', '.join([EXIT_KEYS[k] for k in EXIT_KEYS])
        print(f"\r\nConnected to serial device. Exit key: {esc_keys}\r\n",
              flush=True)

        exit_requested = threading.Event()
        import os
        tty_fd = os.open('/dev/tty', os.O_RDWR)
        old_settings = termios.tcgetattr(tty_fd)
        import termios as termios_module
        termios_module.tcflush(tty_fd, termios_module.TCIFLUSH)
        tty.setraw(tty_fd)

        # Input batching
        input_buffer = []
        last_input_time = time.time()
        batch_interval = 0.02  # 20ms

        def flush_input(force=False):
            nonlocal input_buffer, last_input_time
            if not input_buffer:
                return
            if not force and (time.time() - last_input_time) < batch_interval:
                return
            data = ''.join(input_buffer)
            input_buffer = []
            last_input_time = time.time()
            try:
                write_client.serial_write(data, add_newline=False)
            except Exception as e:
                logging.error(f"Serial write error (batch): {e}")

        def input_thread():
            import select
            nonlocal input_buffer, last_input_time
            while not exit_requested.is_set():
                try:
                    ready, _, _ = select.select([tty_fd], [], [], 0.05)
                except Exception as e:
                    logging.error(f"select() error: {e}")
                    continue
                if not ready:
                    flush_input(force=True)  # time-based flush
                    continue
                try:
                    chars = os.read(tty_fd, 64)  # read a slightly larger burst
                    if not chars:
                        continue
                    decoded = chars.decode('utf-8', errors='replace')
                except Exception as e:
                    logging.error(f"Read error: {e}")
                    continue
                for ch in decoded:
                    if ch in EXIT_KEYS:
                        exit_requested.set()
                        flush_input(force=True)
                        return
                    o = ord(ch)
                    # Send non-exit control characters immediately (except CR/LF which we may coalesce)
                    if o < 0x20 and ch not in ('\r', '\n'):
                        try:
                            write_client.serial_write(ch, add_newline=False)
                        except Exception as e:
                            logging.error(f"Immediate control write error: {e}")
                        continue
                    # Normal characters get batched
                    input_buffer.append(ch)
                flush_input()  # attempt flush (will obey interval)
            logging.debug("Input thread exiting")

        def output_thread():
            # Use raw stream for better interactive fidelity
            def handle_raw(chunk: bytes):
                if exit_requested.is_set():
                    return
                processed = emulator.process_data(chunk)
                print(processed, end='', flush=True)
            try:
                stream_client.raw_stream(handle_raw, stop_event=exit_requested)
            except Exception as e:
                if not exit_requested.is_set():
                    logging.error(f"Output thread error: {e}")
                    exit_requested.set()

        it = threading.Thread(target=input_thread, daemon=True)
        ot = threading.Thread(target=output_thread, daemon=True)
        it.start()
        ot.start()
        try:
            exit_requested.wait()
        except KeyboardInterrupt:
            exit_requested.set()
        it.join(timeout=0.5)
        ot.join(timeout=0.5)
        try:
            termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_settings)
            os.close(tty_fd)
        except Exception:
            pass
        print("\r\n[Exiting terminal mode]\r\n", file=sys.stderr)
    except Exception as e:
        try:
            if tty_fd is not None and old_settings is not None:
                termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_settings)
                import os
                os.close(tty_fd)
        except Exception:
            pass
        print(f"\r\nError: {e}\r\n", file=sys.stderr)
        sys.exit(1)
    finally:
        stream_client.close()
        write_client.close()


def main():
    parser = argparse.ArgumentParser(description="ttygeist CLI - interact with serial console")
    parser.add_argument('-c', '--config',
                        help='Path to config file',
                        default=None)
    subparsers = parser.add_subparsers(dest='command',
                                       help='Command to execute')
    subparsers.required = True
    show_p = subparsers.add_parser('show',
                                   help='Show last N lines from buffer')
    show_p.add_argument('-n', '--lines',
                        type=int,
                        default=32)
    show_p.set_defaults(func=cmd_show)

    tail_p = subparsers.add_parser('tail',
                                   help='Tail buffer in real-time')
    tail_p.add_argument('-n', '--lines',
                        type=int,
                        default=0)
    tail_p.set_defaults(func=cmd_tail)

    status_p = subparsers.add_parser('status',
                                     help='Show connection and buffer status')
    status_p.set_defaults(func=cmd_status)

    term_p = subparsers.add_parser('terminal',
                                   help='Interactive terminal mode')
    term_p.add_argument('-n', '--lines',
                        type=int,
                        default=10)
    term_p.add_argument('-v', '--verbose',
                        action='store_true')
    term_p.set_defaults(func=cmd_terminal)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
