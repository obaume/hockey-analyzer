from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent

from hockey_analyzer.ui.keys import key_string, key_string_from_event


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


def test_key_string_from_event_strips_altgr_ctrl_alt_combo():
    # On Windows, AltGr (used e.g. on QWERTZ layouts to type "]" via AltGr+è)
    # is synthesized as a simultaneous Ctrl+Alt press. Qt still reports both
    # modifiers on the QKeyEvent even though the resolved key already
    # accounts for AltGr, so this must match the plain "]" binding.
    altgr_modifiers = (
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    )
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_BracketRight,
        altgr_modifiers,
    )

    assert key_string_from_event(event) == key_string(Qt.Key.Key_BracketRight)


def test_key_string_from_event_keeps_plain_modifiers():
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_O,
        Qt.KeyboardModifier.ControlModifier,
    )

    assert key_string_from_event(event) == key_string(
        Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier
    )
