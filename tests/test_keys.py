from __future__ import annotations

from PySide6.QtCore import Qt

from hockey_analyzer.ui.keys import key_string


def test_key_string_is_stable_for_a_plain_key():
    assert key_string(Qt.Key.Key_Space) == key_string(Qt.Key.Key_Space)


def test_key_string_distinguishes_modifiers():
    plain = key_string(Qt.Key.Key_O)
    with_ctrl = key_string(Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)

    assert plain != with_ctrl


def test_key_string_matches_expected_portable_text():
    assert key_string(Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier) == "Ctrl+O"
    assert key_string(Qt.Key.Key_Space) == "Space"
    assert key_string(Qt.Key.Key_Left) == "Left"
