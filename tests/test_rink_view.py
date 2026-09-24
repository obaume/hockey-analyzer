from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtCore import Qt as QtCore
from PySide6.QtWidgets import QDialog

from hockey_analyzer.domain.enums import RinkType, ShotOutcome, ShotType
from hockey_analyzer.ui.rink_view import (
    RinkClickDialog,
    RinkDiagramWidget,
    ShotAttemptCaptureDialog,
)


def _make_widget(qtbot, rink_type=RinkType.IIHF, width=400, height=200):
    widget = RinkDiagramWidget(rink_type)
    qtbot.addWidget(widget)
    widget.resize(width, height)
    widget.show()
    qtbot.waitExposed(widget)
    # A resize doesn't synchronously recompute the aspect-locked Axes box
    # -- give the event loop an extra cycle so the box is settled at its
    # final size before a test clicks against it.
    widget.draw_idle()
    qtbot.wait(50)
    return widget


def _center(widget) -> QPoint:
    return QPoint(widget.width() // 2, widget.height() // 2)


# -- RinkDiagramWidget: rink_type selects the drawn template -----------------


def test_iihf_and_nhl_draw_different_extents(qtbot):
    iihf_widget = _make_widget(qtbot, RinkType.IIHF)
    nhl_widget = _make_widget(qtbot, RinkType.NHL)

    iihf_xlim = iihf_widget._ax.get_xlim()
    nhl_xlim = nhl_widget._ax.get_xlim()

    # IIHF's boards are narrower in length (197ft) than NHL's (200ft) --
    # confirms the two rink_types genuinely draw different templates
    # rather than always falling back to one.
    assert iihf_xlim != nhl_xlim
    assert max(abs(v) for v in iihf_xlim) < max(abs(v) for v in nhl_xlim)


# -- RinkDiagramWidget: pixel -> rink-coordinate math ------------------------


def test_click_near_center_maps_near_center_ice(qtbot):
    widget = _make_widget(qtbot)

    with qtbot.waitSignal(widget.location_clicked) as blocker:
        qtbot.mouseClick(widget, QtCore.MouseButton.LeftButton, pos=_center(widget))

    x, y = blocker.args
    assert x == pytest.approx(0.0, abs=3.0)
    assert y == pytest.approx(0.0, abs=3.0)


def test_click_near_right_edge_maps_to_positive_x(qtbot):
    widget = _make_widget(qtbot)

    with qtbot.waitSignal(widget.location_clicked) as blocker:
        qtbot.mouseClick(
            widget,
            QtCore.MouseButton.LeftButton,
            pos=QPoint(widget.width() - 5, widget.height() // 2),
        )

    x, _ = blocker.args
    assert x > 50.0


def test_click_outside_the_letterboxed_rink_is_ignored(qtbot):
    # A widget much wider than the rink's own aspect ratio leaves blank
    # margin on the sides once matplotlib shrinks the aspect-locked Axes
    # box to fit -- a click landing in that margin must not be
    # interpreted as an on-ice location.
    widget = _make_widget(qtbot, width=900, height=200)

    received: list[tuple[float, float]] = []
    widget.location_clicked.connect(lambda x, y: received.append((x, y)))
    qtbot.mouseClick(widget, QtCore.MouseButton.LeftButton, pos=QPoint(5, 100))

    assert received == []


# -- RinkClickDialog: click-to-accept ----------------------------------------


def test_rink_click_dialog_accepts_itself_on_click(qtbot):
    dialog = RinkClickDialog(RinkType.IIHF)
    qtbot.addWidget(dialog)
    dialog.resize(400, 200)
    dialog.show()
    qtbot.waitExposed(dialog)

    qtbot.mouseClick(
        dialog.rink, QtCore.MouseButton.LeftButton, pos=_center(dialog.rink)
    )
    # matplotlib schedules a deferred idle-redraw on click; let it fire
    # here, while the widget is still alive, rather than leaking into
    # whichever test runs next (a stray fired-late redraw referencing an
    # already-GC'd canvas raises a libshiboken "already deleted" error).
    qtbot.wait(50)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.x == pytest.approx(0.0, abs=3.0)
    assert dialog.y == pytest.approx(0.0, abs=3.0)


def test_rink_click_dialog_uses_the_passed_rink_type(qtbot):
    dialog = RinkClickDialog(RinkType.NHL)
    qtbot.addWidget(dialog)

    assert (
        dialog.rink._ax.get_xlim() != RinkClickDialog(RinkType.IIHF).rink._ax.get_xlim()
    )


# -- ShotAttemptCaptureDialog: combined location + outcome/type -------------


def test_shot_attempt_capture_dialog_exposes_selected_outcome_and_type(qtbot):
    dialog = ShotAttemptCaptureDialog(RinkType.IIHF)
    qtbot.addWidget(dialog)

    index = dialog.outcome_combo.findData(ShotOutcome.GOAL.value)
    dialog.outcome_combo.setCurrentIndex(index)
    index = dialog.shot_type_combo.findData(ShotType.SLAP.value)
    dialog.shot_type_combo.setCurrentIndex(index)

    assert dialog.shot_outcome is ShotOutcome.GOAL
    assert dialog.shot_type is ShotType.SLAP


def test_shot_attempt_capture_dialog_accepts_itself_on_click(qtbot):
    dialog = ShotAttemptCaptureDialog(RinkType.IIHF)
    qtbot.addWidget(dialog)
    dialog.resize(400, 200)
    dialog.show()
    qtbot.waitExposed(dialog)

    qtbot.mouseClick(
        dialog.rink, QtCore.MouseButton.LeftButton, pos=_center(dialog.rink)
    )
    qtbot.wait(50)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.x == pytest.approx(0.0, abs=3.0)
