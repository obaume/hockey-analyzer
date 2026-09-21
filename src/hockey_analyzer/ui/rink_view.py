"""Rink-click capture (ticket 16): a rendered rink diagram the tagger
clicks a point on to record a `faceoff`/`shot_attempt`'s (x, y) location,
plus the two modal prompts `TaggingPanel` opens for it. Rendering and
pixel-to-coordinate math only -- no domain logic lives here, matching
`tagging_panel.py`'s split.

Coordinate system matches `hockey_analyzer.domain.rink`: origin at center
ice, x along the long axis in feet (+/-100), y across the width
(+/-42.5), on a standard 200x85 ft rink.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QVBoxLayout, QWidget

from hockey_analyzer.domain.enums import ShotOutcome, ShotType
from hockey_analyzer.domain.rink import BLUE_LINE_X, GOAL_LINE_X

RINK_HALF_LENGTH = 100.0
RINK_HALF_WIDTH = 42.5


class RinkDiagramWidget(QWidget):
    """Emits `location_clicked(x, y)`, in rink-coordinate feet, for a
    click landing inside the drawn rink. The rink is letterboxed to the
    widget's actual size (its 200:85 aspect ratio is fixed regardless of
    how the widget is resized), so a click outside that drawn area is
    silently ignored rather than clamped to the nearest edge."""

    location_clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 136)

    def _rink_rect(self) -> QRectF:
        aspect = (RINK_HALF_LENGTH * 2) / (RINK_HALF_WIDTH * 2)
        width, height = float(self.width()), float(self.height())
        if height <= 0 or width <= 0:
            return QRectF(0, 0, 0, 0)
        if width / height > aspect:
            rect_height = height
            rect_width = height * aspect
        else:
            rect_width = width
            rect_height = width / aspect
        x0 = (width - rect_width) / 2
        y0 = (height - rect_height) / 2
        return QRectF(x0, y0, rect_width, rect_height)

    def _pixel_to_rink(self, pos: QPointF) -> tuple[float, float] | None:
        rect = self._rink_rect()
        if rect.width() <= 0 or not rect.contains(pos):
            return None
        fraction_x = (pos.x() - rect.left()) / rect.width()
        fraction_y = (pos.y() - rect.top()) / rect.height()
        x = (fraction_x * 2 - 1) * RINK_HALF_LENGTH
        y = (fraction_y * 2 - 1) * RINK_HALF_WIDTH
        return x, y

    def mousePressEvent(self, event: QMouseEvent) -> None:
        result = self._pixel_to_rink(event.position())
        if result is not None:
            self.location_clicked.emit(*result)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        rect = self._rink_rect()
        painter.fillRect(self.rect(), Qt.GlobalColor.darkGray)
        if rect.width() <= 0:
            return
        painter.fillRect(rect, Qt.GlobalColor.white)

        def x_to_px(x: float) -> float:
            return rect.left() + (x + RINK_HALF_LENGTH) / (RINK_HALF_LENGTH * 2) * rect.width()

        painter.setPen(QPen(Qt.GlobalColor.red))
        painter.drawLine(QPointF(x_to_px(0.0), rect.top()), QPointF(x_to_px(0.0), rect.bottom()))
        for goal_line_x in (-GOAL_LINE_X, GOAL_LINE_X):
            painter.drawLine(QPointF(x_to_px(goal_line_x), rect.top()), QPointF(x_to_px(goal_line_x), rect.bottom()))

        painter.setPen(QPen(Qt.GlobalColor.blue))
        for blue_line_x in (-BLUE_LINE_X, BLUE_LINE_X):
            painter.drawLine(QPointF(x_to_px(blue_line_x), rect.top()), QPointF(x_to_px(blue_line_x), rect.bottom()))

        painter.setPen(QPen(Qt.GlobalColor.black))
        painter.drawRect(rect)


class RinkClickDialog(QDialog):
    """Modal "click a point" prompt: shows a `RinkDiagramWidget` and
    accepts itself the instant the tagger clicks a location -- there is
    no separate confirm step, matching the log buttons' single-click-to-
    act feel elsewhere in the tagging panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Click a location")
        self.rink = RinkDiagramWidget(self)
        self.rink.location_clicked.connect(self._on_location_clicked)
        layout = QVBoxLayout(self)
        layout.addWidget(self.rink)
        self.x: float | None = None
        self.y: float | None = None

    def _on_location_clicked(self, x: float, y: float) -> None:
        self.x = x
        self.y = y
        self.accept()


class ShotAttemptCaptureDialog(QDialog):
    """Combined "click location + pick outcome/type" prompt for
    `shot_attempt`. Unlike `faceoff`, its outcome and shot type are
    required at the DB level from the instant the event is created (see
    `TaggingSession.log_event`), so both must be captured here rather than
    left to a later inline edit."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shot attempt")
        self.rink = RinkDiagramWidget(self)
        self.rink.location_clicked.connect(self._on_location_clicked)

        # Item data is each member's plain `.value` string, not the enum
        # member itself -- PySide6's QVariant round-trip through a
        # QComboBox's userData silently drops a str-subclassed Enum back
        # to a bare `str` (losing its type), so the `shot_outcome`/
        # `shot_type` properties below reconstruct the enum explicitly.
        self.outcome_combo = QComboBox()
        for outcome in ShotOutcome:
            self.outcome_combo.addItem(outcome.value, outcome.value)
        self.shot_type_combo = QComboBox()
        for shot_type in ShotType:
            self.shot_type_combo.addItem(shot_type.value, shot_type.value)

        form = QFormLayout()
        form.addRow("Outcome", self.outcome_combo)
        form.addRow("Shot type", self.shot_type_combo)

        layout = QVBoxLayout(self)
        layout.addWidget(self.rink)
        layout.addLayout(form)

        self.x: float | None = None
        self.y: float | None = None

    def _on_location_clicked(self, x: float, y: float) -> None:
        self.x = x
        self.y = y
        self.accept()

    @property
    def shot_outcome(self) -> ShotOutcome:
        return ShotOutcome(self.outcome_combo.currentData())

    @property
    def shot_type(self) -> ShotType:
        return ShotType(self.shot_type_combo.currentData())
