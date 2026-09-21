from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtCore import Qt as QtCore
from PySide6.QtWidgets import QDialog

from hockey_analyzer.domain.enums import ShotOutcome, ShotType
from hockey_analyzer.ui.rink_view import RinkClickDialog, RinkDiagramWidget, ShotAttemptCaptureDialog


def _make_widget(qtbot, width=400, height=170):
    widget = RinkDiagramWidget()
    qtbot.addWidget(widget)
    widget.resize(width, height)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


# -- RinkDiagramWidget: pixel -> rink-coordinate math ------------------------


def test_click_at_center_maps_to_center_ice(qtbot):
    widget = _make_widget(qtbot)

    with qtbot.waitSignal(widget.location_clicked) as blocker:
        qtbot.mouseClick(widget, QtCore.MouseButton.LeftButton, pos=QPoint(200, 85))

    x, y = blocker.args
    assert x == pytest.approx(0.0, abs=1.0)
    assert y == pytest.approx(0.0, abs=1.0)


def test_click_near_right_edge_maps_to_positive_x(qtbot):
    widget = _make_widget(qtbot)

    with qtbot.waitSignal(widget.location_clicked) as blocker:
        qtbot.mouseClick(widget, QtCore.MouseButton.LeftButton, pos=QPoint(398, 85))

    x, _ = blocker.args
    assert x > 90.0


def test_click_near_top_edge_maps_to_negative_y(qtbot):
    widget = _make_widget(qtbot)

    with qtbot.waitSignal(widget.location_clicked) as blocker:
        qtbot.mouseClick(widget, QtCore.MouseButton.LeftButton, pos=QPoint(200, 2))

    _, y = blocker.args
    assert y < -35.0


def test_click_outside_the_letterboxed_rink_is_ignored(qtbot):
    # A widget wider than the rink's 200:85 aspect ratio letterboxes the
    # rink, leaving bars on the sides -- a click landing in a bar must not
    # be interpreted as an on-ice location.
    widget = _make_widget(qtbot, width=600, height=170)

    received: list[tuple[float, float]] = []
    widget.location_clicked.connect(lambda x, y: received.append((x, y)))
    qtbot.mouseClick(widget, QtCore.MouseButton.LeftButton, pos=QPoint(20, 85))

    assert received == []


# -- RinkClickDialog: click-to-accept ----------------------------------------


def test_rink_click_dialog_accepts_itself_on_click(qtbot):
    dialog = RinkClickDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    center = QPoint(dialog.rink.width() // 2, dialog.rink.height() // 2)
    qtbot.mouseClick(dialog.rink, QtCore.MouseButton.LeftButton, pos=center)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.x == pytest.approx(0.0, abs=2.0)
    assert dialog.y == pytest.approx(0.0, abs=2.0)


# -- ShotAttemptCaptureDialog: combined location + outcome/type -------------


def test_shot_attempt_capture_dialog_exposes_selected_outcome_and_type(qtbot):
    dialog = ShotAttemptCaptureDialog()
    qtbot.addWidget(dialog)

    index = dialog.outcome_combo.findData(ShotOutcome.GOAL.value)
    dialog.outcome_combo.setCurrentIndex(index)
    index = dialog.shot_type_combo.findData(ShotType.SLAP.value)
    dialog.shot_type_combo.setCurrentIndex(index)

    assert dialog.shot_outcome is ShotOutcome.GOAL
    assert dialog.shot_type is ShotType.SLAP


def test_shot_attempt_capture_dialog_accepts_itself_on_click(qtbot):
    dialog = ShotAttemptCaptureDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    center = QPoint(dialog.rink.width() // 2, dialog.rink.height() // 2)
    qtbot.mouseClick(dialog.rink, QtCore.MouseButton.LeftButton, pos=center)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.x == pytest.approx(0.0, abs=2.0)
