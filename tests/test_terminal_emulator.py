"""Tests for TerminalEmulator."""

import pytest

from ttygeist.terminal_emulator import ParserState, TerminalEmulator


@pytest.fixture()
def emu():
    """Fresh TerminalEmulator with default settings."""
    return TerminalEmulator()


class TestBasicText:
    def test_plain_ascii(self, emu):
        assert emu.process_data(b"hello") == "hello"

    def test_printable_range(self, emu):
        text = "".join(chr(c) for c in range(0x20, 0x7F))
        assert emu.process_string(text) == text

    def test_empty_input(self, emu):
        assert emu.process_data(b"") == ""


class TestControlCharacters:
    @pytest.mark.parametrize(
        "byte_val, expected",
        [
            (0x07, "\a"),
            (0x08, "\b"),
            (0x09, "\t"),
            (0x0A, "\n"),
            (0x0D, "\r"),
        ],
        ids=["bell", "backspace", "tab", "lf", "cr"],
    )
    def test_recognized_controls(self, emu, byte_val, expected):
        assert emu.process_byte(byte_val) == expected

    @pytest.mark.parametrize("byte_val", [0x00, 0x01], ids=["null", "soh"])
    def test_stripped_controls(self, emu, byte_val):
        assert emu.process_byte(byte_val) is None


class TestCRLFHandling:
    def test_crlf_emits_both(self, emu):
        assert emu.process_data(b"\r\n") == "\r\n"

    def test_cr_alone(self, emu):
        assert emu.process_data(b"abc\rdef") == "abc\rdef"

    def test_lf_alone(self, emu):
        assert emu.process_data(b"abc\ndef") == "abc\ndef"


class TestEscapeSequences:
    def test_esc_transitions_state(self, emu):
        emu.process_byte(0x1B)
        assert emu.state == ParserState.ESC

    def test_csi_transitions_state(self, emu):
        emu.process_byte(0x1B)
        emu.process_byte(ord("["))
        assert emu.state == ParserState.CSI

    def test_csi_completed_returns_to_normal(self, emu):
        emu.process_data(b"\x1b[1m")
        assert emu.state == ParserState.NORMAL

    def test_unknown_esc_returns_to_normal(self, emu):
        emu.process_data(b"\x1b!")
        assert emu.state == ParserState.NORMAL


class TestSingleEscSequences:
    @pytest.mark.parametrize(
        "seq, expected",
        [
            (b"\x1bc", "\x1bc"),
            (b"\x1bD", "\x1bD"),
            (b"\x1bE", "\x1bE"),
            (b"\x1bM", "\x1bM"),
            (b"\x1bH", ""),
            (b"\x1bZ", ""),
            (b"\x1b7", ""),
            (b"\x1b8", ""),
        ],
        ids=["reset", "linefeed", "nextline", "reverse_lf", "tab_stop", "identify", "save_cursor", "restore_cursor"],
    )
    def test_single_esc(self, emu, seq, expected):
        assert emu.process_data(seq) == expected


class TestSGR:
    def test_sgr_reset(self):
        emu = TerminalEmulator(color_support=True)
        assert emu.process_data(b"\x1b[0m") == "\x1b[0m"

    def test_sgr_bold(self):
        emu = TerminalEmulator(color_support=True)
        assert emu.process_data(b"\x1b[1m") == "\x1b[1m"

    def test_sgr_color(self):
        emu = TerminalEmulator(color_support=True)
        assert emu.process_data(b"\x1b[31m") == "\x1b[31m"

    def test_sgr_multi_param(self):
        emu = TerminalEmulator(color_support=True)
        assert emu.process_data(b"\x1b[1;31;42m") == "\x1b[1;31;42m"

    def test_sgr_no_params_defaults_to_reset(self):
        emu = TerminalEmulator(color_support=True)
        assert emu.process_data(b"\x1b[m") == "\x1b[0m"

    def test_sgr_stripped_without_color_support(self):
        emu = TerminalEmulator(color_support=False)
        assert emu.process_data(b"\x1b[31m") == ""


class TestCursorMovement:
    @pytest.mark.parametrize(
        "seq, expected",
        [
            (b"\x1b[5A", "\x1b[5A"),
            (b"\x1b[3B", "\x1b[3B"),
            (b"\x1b[10C", "\x1b[10C"),
            (b"\x1b[2D", "\x1b[2D"),
            (b"\x1b[10;20H", "\x1b[10;20H"),
            (b"\x1b[5G", "\x1b[5G"),
        ],
        ids=["up", "down", "forward", "back", "position", "column"],
    )
    def test_cursor(self, emu, seq, expected):
        assert emu.process_data(seq) == expected


class TestScreenOps:
    @pytest.mark.parametrize(
        "seq, expected",
        [
            (b"\x1b[2J", "\x1b[2J"),
            (b"\x1b[K", "\x1b[K"),
            (b"\x1b[1K", "\x1b[1K"),
        ],
        ids=["clear_screen", "erase_line", "erase_line_param"],
    )
    def test_screen_op(self, emu, seq, expected):
        assert emu.process_data(seq) == expected


class TestIgnoredCSI:
    @pytest.mark.parametrize(
        "seq",
        [
            b"\x1b[1;24r",
            b"\x1b[s",
            b"\x1b[u",
            b"\x1b[?25h",
            b"\x1b[?25l",
        ],
        ids=["scroll_region", "cursor_save", "cursor_restore", "mode_set", "mode_reset"],
    )
    def test_ignored_csi(self, emu, seq):
        assert emu.process_data(seq) == ""


class TestMalformedSequences:
    def test_malformed_csi_params(self, emu):
        emu.process_data(b"\x1b[abc;defm")
        assert emu.state == ParserState.NORMAL

    def test_mixed_text_and_escapes(self, emu):
        result = emu.process_data(b"hello\x1b[31mworld\x1b[0m!")
        assert "hello" in result
        assert "world" in result
        assert "!" in result


class TestUtf8MultiByte:
    """UTF-8 multi-byte sequence handling in process_byte / process_data."""

    def test_two_byte_sequence(self, emu):
        # U+00E9 LATIN SMALL LETTER E WITH ACUTE = 0xC3 0xA9
        assert emu.process_data(b"\xc3\xa9") == "\u00e9"

    def test_three_byte_sequence(self, emu):
        # U+2603 SNOWMAN = 0xE2 0x98 0x83
        assert emu.process_data(b"\xe2\x98\x83") == "\u2603"

    def test_four_byte_emoji(self, emu):
        # U+1F600 GRINNING FACE = 0xF0 0x9F 0x98 0x80
        assert emu.process_data(b"\xf0\x9f\x98\x80") == "\U0001f600"

    def test_mixed_ascii_and_multibyte(self, emu):
        # "hi" + snowman + "!" = normal interleaving
        result = emu.process_data(b"hi\xe2\x98\x83!")
        assert result == "hi\u2603!"

    def test_multiple_emoji_consecutive(self, emu):
        # Two emoji back-to-back
        data = "\U0001f600\U0001f609".encode()
        assert emu.process_data(data) == "\U0001f600\U0001f609"

    def test_orphan_continuation_byte_skipped(self, emu):
        # A bare continuation byte (0x80-0xBF) outside a sequence
        result = emu.process_data(b"\x80hello")
        assert result == "hello"

    def test_broken_sequence_recovers(self, emu):
        # Start a 3-byte sequence but follow with ASCII instead of continuation
        # 0xE2 expects 2 continuation bytes; 'A' (0x41) breaks it
        result = emu.process_data(b"\xe2A")
        assert result == "A"

    def test_utf8_interleaved_with_escape(self, emu):
        # emoji then an ANSI SGR sequence then ASCII
        data = "\U0001f600".encode() + b"\x1b[31m" + b"red"
        result = emu.process_data(data)
        assert "\U0001f600" in result
        assert "red" in result

    def test_process_string_roundtrip(self, emu):
        # process_string encodes to UTF-8 internally, should round-trip
        text = "caf\u00e9 \u2603 \U0001f600"
        assert emu.process_string(text) == text


class TestProcessString:
    def test_convenience_method(self, emu):
        assert emu.process_string("plain text") == "plain text"

    def test_with_escapes(self):
        emu = TerminalEmulator(color_support=True)
        result = emu.process_string("\x1b[1mbold\x1b[0m")
        assert "bold" in result
        assert "\x1b[1m" in result
