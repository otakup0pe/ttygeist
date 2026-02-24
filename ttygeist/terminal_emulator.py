"""Terminal emulator for handling escape sequences in serial output."""

import logging
import sys
from enum import Enum

logger = logging.getLogger(__name__)


class ParserState(Enum):
    """Parser state machine states."""

    NORMAL = 0
    ESC = 1
    CSI = 2


class TerminalEmulator:
    """Basic terminal emulator for handling ANSI/VT100 escape sequences.

    This is NOT a full terminal emulator - it handles common escape sequences
    for basic terminal interaction (colors, cursor movement, screen clearing)
    and strips/logs unknown sequences.

    CR/LF handling tweak:
    Previous implementation suppressed LF after CR, causing lines to overwrite
    when devices emitted CRLF for newlines. Now we emit both CR and LF; this
    preserves proper line advancement on typical CRLF serial logs.
    """

    def __init__(self, color_support: bool = True):
        """Initialize terminal emulator.

        Args:
            color_support: Whether to emit color escape codes
        """
        self.color_support = color_support
        self.state = ParserState.NORMAL
        self.csi_buffer = ""
        self.output_buffer = ""
        self.last_was_cr = False
        # UTF-8 multi-byte accumulation buffer
        self._utf8_buf = bytearray()
        self._utf8_expected = 0

    def process_byte(self, byte: int) -> str | None:
        """Process a single byte through the state machine.

        Handles UTF-8 multi-byte sequences by accumulating continuation
        bytes and decoding the complete sequence before emitting.

        Args:
            byte: Byte to process (0-255)

        Returns:
            Output string if any should be emitted, None otherwise
        """
        # UTF-8 continuation byte (10xxxxxx) while accumulating
        if self._utf8_expected > 0:
            if 0x80 <= byte <= 0xBF:
                self._utf8_buf.append(byte)
                self._utf8_expected -= 1
                if self._utf8_expected == 0:
                    # Complete sequence -- decode and emit
                    try:
                        result = bytes(self._utf8_buf).decode("utf-8")
                    except UnicodeDecodeError:
                        result = None
                        logger.debug(f"Invalid UTF-8 sequence: {self._utf8_buf.hex()}")
                    self._utf8_buf.clear()
                    self.last_was_cr = False
                    return result
                return None
            else:
                # Broken sequence -- discard buffer, reprocess this byte
                logger.debug(f"Broken UTF-8 sequence: {self._utf8_buf.hex()}")
                self._utf8_buf.clear()
                self._utf8_expected = 0
                # Fall through to process this byte normally

        # Start of multi-byte UTF-8 sequence
        if byte >= 0xC0:
            self._utf8_buf.clear()
            self._utf8_buf.append(byte)
            if byte < 0xE0:
                self._utf8_expected = 1  # 2-byte sequence
            elif byte < 0xF0:
                self._utf8_expected = 2  # 3-byte sequence
            elif byte < 0xF8:
                self._utf8_expected = 3  # 4-byte sequence
            else:
                # Invalid lead byte
                self._utf8_buf.clear()
                self._utf8_expected = 0
                logger.debug(f"Invalid UTF-8 lead byte: 0x{byte:02x}")
                return None
            return None

        # Bare continuation byte outside a sequence -- skip it
        if 0x80 <= byte <= 0xBF:
            logger.debug(f"Orphan UTF-8 continuation byte: 0x{byte:02x}")
            return None

        # ASCII byte (0x00-0x7F) -- process through state machine
        char = chr(byte)

        if self.state == ParserState.NORMAL:
            return self._handle_normal(byte, char)
        elif self.state == ParserState.ESC:
            return self._handle_esc(byte, char)
        elif self.state == ParserState.CSI:
            return self._handle_csi(byte, char)

    def process_data(self, data: bytes) -> str:
        """Process a chunk of data.

        Args:
            data: Bytes to process

        Returns:
            Processed output string
        """
        output = []
        for byte in data:
            result = self.process_byte(byte)
            if result is not None:
                output.append(result)
        return "".join(output)

    def process_string(self, data: str) -> str:
        """Process a string (convenience method)."""
        return self.process_data(data.encode("utf-8", errors="replace"))

    def _handle_normal(self, byte: int, char: str) -> str | None:
        """Handle NORMAL state."""
        if byte == 0x1B:  # ESC
            self.state = ParserState.ESC
            self.last_was_cr = False
            return None
        elif byte == 0x07:  # BEL
            self.last_was_cr = False
            return "\a"
        elif byte == 0x08:  # BS
            self.last_was_cr = False
            return "\b"
        elif byte == 0x09:  # TAB
            self.last_was_cr = False
            return "\t"
        elif byte == 0x0A:  # LF
            # Emit LF regardless of previous CR to avoid line overwrite issues
            self.last_was_cr = False
            return "\n"
        elif byte == 0x0D:  # CR
            # Emit CR; many terminals treat CRLF as newline,
            # emitting both is safe
            self.last_was_cr = True
            return "\r"
        elif byte < 0x20 and byte not in (0x07, 0x08, 0x09, 0x0A, 0x0D):
            logger.debug(f"Stripped control character: 0x{byte:02x}")
            self.last_was_cr = False
            return None
        else:
            self.last_was_cr = False
            return char

    def _handle_esc(self, byte: int, char: str) -> str | None:
        """Handle ESC state."""
        if char == "[":
            self.state = ParserState.CSI
            self.csi_buffer = ""
            return None
        elif char in "cDEHMZ78":
            self.state = ParserState.NORMAL
            return self._handle_single_esc(char)
        else:
            logger.warning(f"Unknown escape sequence: ESC {char} (0x{byte:02x})")
            self.state = ParserState.NORMAL
            return None

    def _handle_csi(self, byte: int, char: str) -> str | None:
        """Handle CSI state."""
        if char.isalpha() or char in "@`":
            self.state = ParserState.NORMAL
            result = self._process_csi(self.csi_buffer + char)
            self.csi_buffer = ""
            return result
        else:
            self.csi_buffer += char
            return None

    def _handle_single_esc(self, char: str) -> str | None:
        """Handle single-character escape sequences."""
        escape_map = {
            "c": "\x1bc",  # Reset terminal - pass through
            "D": "\x1bD",  # Line feed - pass through
            "E": "\x1bE",  # Next line - pass through
            "H": None,  # Set tab stop - ignore
            "M": "\x1bM",  # Reverse line feed - pass through
            "Z": None,  # Identify terminal - ignore
            "7": None,  # Save cursor position - ignore
            "8": None,  # Restore cursor position - ignore
        }
        result = escape_map.get(char)
        if result is None and char not in escape_map:
            logger.warning(f"Unknown single ESC sequence: ESC {char}")
        return result

    def _process_csi(self, sequence: str) -> str | None:
        """Process a complete CSI sequence."""
        if not sequence:
            return None
        final_char = sequence[-1]
        params_str = sequence[:-1]
        params = []
        if params_str:
            try:
                params = [int(p) if p else 0 for p in params_str.split(";")]
            except ValueError:
                logger.warning(f"Malformed CSI parameters: {params_str}")
                return None
        if final_char == "m":
            return self._handle_sgr(params)
        elif final_char in "ABCDEFGHJ":
            return self._handle_cursor_screen(final_char, params)
        elif final_char == "K":
            return self._handle_erase_line(params)
        elif final_char in "r":
            logger.debug(f"Ignoring CSI scrolling region: {sequence}")
            return None
        elif final_char in "su":
            logger.debug(f"Ignoring CSI cursor save/restore: {sequence}")
            return None
        elif final_char in "hl":
            logger.debug(f"Ignoring CSI mode: {sequence}")
            return None
        else:
            logger.warning(f"Unknown CSI sequence: CSI {sequence}")
            return None

    def _handle_sgr(self, params: list) -> str | None:
        """Handle SGR (Select Graphic Rendition) sequences."""
        if not self.color_support:
            return None
        if not params:
            params = [0]
        return f"\x1b[{';'.join(str(p) for p in params)}m"

    def _handle_cursor_screen(self, command: str, params: list) -> str | None:
        """Handle cursor movement and screen operations."""
        param_str = ";".join(str(p) for p in params) if params else ""
        cursor_commands = {
            "A": f"\x1b[{param_str}A",
            "B": f"\x1b[{param_str}B",
            "C": f"\x1b[{param_str}C",
            "D": f"\x1b[{param_str}D",
            "E": f"\x1b[{param_str}E",
            "F": f"\x1b[{param_str}F",
            "G": f"\x1b[{param_str}G",
            "H": f"\x1b[{param_str}H",
            "J": f"\x1b[{param_str}J",
        }
        return cursor_commands.get(command)

    def _handle_erase_line(self, params: list) -> str | None:
        """Handle erase in line sequences."""
        param_str = ";".join(str(p) for p in params) if params else ""
        return f"\x1b[{param_str}K"


def detect_terminal_capabilities() -> tuple[bool, bool]:
    """Detect terminal color and unicode support."""
    color_support = False
    unicode_support = False
    if not sys.stdout.isatty():
        return False, False
    import os

    term = os.environ.get("TERM", "")
    colorterm = os.environ.get("COLORTERM", "")
    if (
        term.endswith("color")
        or "256color" in term
        or "truecolor" in term
        or colorterm in ("truecolor", "24bit")
        or term
        in ("xterm", "xterm-color", "xterm-256color", "screen", "screen-256color", "tmux", "tmux-256color", "linux")
    ):
        color_support = True
    import locale

    try:
        encoding = locale.getpreferredencoding()
        if "utf" in encoding.lower() or "utf8" in encoding.lower():
            unicode_support = True
    except Exception:
        pass
    lang = os.environ.get("LANG", "")
    if "utf" in lang.lower() or "utf8" in lang.lower():
        unicode_support = True
    return color_support, unicode_support
