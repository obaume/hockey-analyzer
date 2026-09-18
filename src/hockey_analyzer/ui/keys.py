"""Canonical key-string encoding shared between shortcut registration and
`QKeyEvent` dispatch, so the two always agree on what a given key press is
called.
"""

from __future__ import annotations

from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence


def key_string(
    key: Qt.Key, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
) -> str:
    return _combination_string(QKeyCombination(modifiers, key))


def key_string_from_event(event: QKeyEvent) -> str:
    return _combination_string(event.keyCombination())


def _combination_string(combination: QKeyCombination) -> str:
    return QKeySequence(combination).toString(QKeySequence.SequenceFormat.PortableText)
