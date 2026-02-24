"""Tests for TerminalEmulator."""

from ttygeist.terminal_emulator import ParserState, TerminalEmulator


class TestBasicText:
    def test_plain_ascii(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"hello") == "hello"

    def test_printable_range(self):
        emu = TerminalEmulator()
        text = "".join(chr(c) for c in range(0x20, 0x7F))
        assert emu.process_string(text) == text

    def test_empty_input(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"") == ""


class TestControlCharacters:
    def test_bell(self):
        emu = TerminalEmulator()
        assert emu.process_byte(0x07) == "\a"

    def test_backspace(self):
        emu = TerminalEmulator()
        assert emu.process_byte(0x08) == "\b"

    def test_tab(self):
        emu = TerminalEmulator()
        assert emu.process_byte(0x09) == "\t"

    def test_lf(self):
        emu = TerminalEmulator()
        assert emu.process_byte(0x0A) == "\n"

    def test_cr(self):
        emu = TerminalEmulator()
        assert emu.process_byte(0x0D) == "\r"

    def test_unknown_control_stripped(self):
        emu = TerminalEmulator()
        # 0x01 (SOH) should be stripped
        assert emu.process_byte(0x01) is None

    def test_null_stripped(self):
        emu = TerminalEmulator()
        assert emu.process_byte(0x00) is None


class TestCRLFHandling:
    def test_crlf_emits_both(self):
        emu = TerminalEmulator()
        result = emu.process_data(b"\r\n")
        assert result == "\r\n"

    def test_cr_alone(self):
        emu = TerminalEmulator()
        result = emu.process_data(b"abc\rdef")
        assert result == "abc\rdef"

    def test_lf_alone(self):
        emu = TerminalEmulator()
        result = emu.process_data(b"abc\ndef")
        assert result == "abc\ndef"


class TestEscapeSequences:
    def test_esc_transitions_state(self):
        emu = TerminalEmulator()
        emu.process_byte(0x1B)
        assert emu.state == ParserState.ESC

    def test_csi_transitions_state(self):
        emu = TerminalEmulator()
        emu.process_byte(0x1B)
        emu.process_byte(ord("["))
        assert emu.state == ParserState.CSI

    def test_csi_completed_returns_to_normal(self):
        emu = TerminalEmulator()
        emu.process_data(b"\x1b[1m")
        assert emu.state == ParserState.NORMAL

    def test_unknown_esc_returns_to_normal(self):
        emu = TerminalEmulator()
        emu.process_data(b"\x1b!")
        assert emu.state == ParserState.NORMAL


class TestSingleEscSequences:
    def test_esc_c_reset(self):
        emu = TerminalEmulator()
        result = emu.process_data(b"\x1bc")
        assert result == "\x1bc"

    def test_esc_D_linefeed(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1bD") == "\x1bD"

    def test_esc_E_nextline(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1bE") == "\x1bE"

    def test_esc_M_reverse_linefeed(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1bM") == "\x1bM"

    def test_esc_H_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1bH") == ""

    def test_esc_Z_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1bZ") == ""

    def test_esc_7_save_cursor_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b7") == ""

    def test_esc_8_restore_cursor_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b8") == ""


class TestSGR:
    def test_sgr_reset(self):
        emu = TerminalEmulator(color_support=True)
        result = emu.process_data(b"\x1b[0m")
        assert result == "\x1b[0m"

    def test_sgr_bold(self):
        emu = TerminalEmulator(color_support=True)
        result = emu.process_data(b"\x1b[1m")
        assert result == "\x1b[1m"

    def test_sgr_color(self):
        emu = TerminalEmulator(color_support=True)
        result = emu.process_data(b"\x1b[31m")
        assert result == "\x1b[31m"

    def test_sgr_multi_param(self):
        emu = TerminalEmulator(color_support=True)
        result = emu.process_data(b"\x1b[1;31;42m")
        assert result == "\x1b[1;31;42m"

    def test_sgr_no_params_defaults_to_reset(self):
        emu = TerminalEmulator(color_support=True)
        result = emu.process_data(b"\x1b[m")
        assert result == "\x1b[0m"

    def test_sgr_stripped_without_color_support(self):
        emu = TerminalEmulator(color_support=False)
        result = emu.process_data(b"\x1b[31m")
        assert result == ""


class TestCursorMovement:
    def test_cursor_up(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[5A") == "\x1b[5A"

    def test_cursor_down(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[3B") == "\x1b[3B"

    def test_cursor_forward(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[10C") == "\x1b[10C"

    def test_cursor_back(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[2D") == "\x1b[2D"

    def test_cursor_position(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[10;20H") == "\x1b[10;20H"

    def test_cursor_column(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[5G") == "\x1b[5G"


class TestScreenOps:
    def test_clear_screen(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[2J") == "\x1b[2J"

    def test_erase_line(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[K") == "\x1b[K"

    def test_erase_line_with_param(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[1K") == "\x1b[1K"


class TestIgnoredCSI:
    def test_scroll_region_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[1;24r") == ""

    def test_cursor_save_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[s") == ""

    def test_cursor_restore_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[u") == ""

    def test_mode_set_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[?25h") == ""

    def test_mode_reset_ignored(self):
        emu = TerminalEmulator()
        assert emu.process_data(b"\x1b[?25l") == ""


class TestMalformedSequences:
    def test_malformed_csi_params(self):
        emu = TerminalEmulator()
        # Non-numeric CSI params
        emu.process_data(b"\x1b[abc;defm")
        # Should handle gracefully (return None for the sequence)
        assert emu.state == ParserState.NORMAL

    def test_mixed_text_and_escapes(self):
        emu = TerminalEmulator()
        result = emu.process_data(b"hello\x1b[31mworld\x1b[0m!")
        assert "hello" in result
        assert "world" in result
        assert "!" in result


class TestProcessString:
    def test_convenience_method(self):
        emu = TerminalEmulator()
        assert emu.process_string("plain text") == "plain text"

    def test_with_escapes(self):
        emu = TerminalEmulator(color_support=True)
        result = emu.process_string("\x1b[1mbold\x1b[0m")
        assert "bold" in result
        assert "\x1b[1m" in result
