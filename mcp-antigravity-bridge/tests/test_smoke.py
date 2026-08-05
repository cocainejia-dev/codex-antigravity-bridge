"""Smoke tests that do not require an installed agy binary."""

from codex_agy_bridge.agy_runner import clean_agy_output, find_agy


def test_clean_agy_output_removes_ansi():
    raw = "\x1b[32mOK\x1b[0m\r\n\x1b[?25l"
    assert clean_agy_output(raw) == "OK"


def test_clean_agy_output_drops_pure_chrome_lines():
    raw = "┌────────┐\nhello\n└────────┘"
    assert clean_agy_output(raw) == "hello"


def test_clean_agy_output_keeps_code_indentation():
    raw = "def f():\n    return 1"
    assert clean_agy_output(raw) == "def f():\n    return 1"


def test_find_agy_does_not_raise():
    # Should return a path or None, never raise.
    find_agy()