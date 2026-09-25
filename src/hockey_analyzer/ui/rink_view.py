"""Rink-click capture (ticket 16, rebuilt on `hockey_rink` per ADR-0009): a
rendered rink diagram the tagger clicks a point on to record a
`faceoff`/`shot_attempt`'s (x, y) location, plus the two modal prompts
`TaggingPanel` opens for it. Rendering is delegated to `hockey_rink`'s
`IIHFRink`/`NHLRink` templates, drawn onto a matplotlib `FigureCanvasQTAgg`
embedded here -- `domain/rink.py` remains the sole source of truth for the
geometry `zone`/`high_danger` derive from (ADR-0008); this module never
computes or asserts any rink geometry of its own beyond picking which
`hockey_rink` template to draw, by the tagged game's `RinkType`.
`build_rink` is also what a report's baked shot-map chart draws on
(`report_charts`, ticket 26).

Coordinate system matches `hockey_analyzer.domain.rink`: origin at center
ice, x along the long axis in feet, y across the width -- `hockey_rink`'s
own convention already agrees with ours for both supported standards, so
no coordinate translation happens here.
"""

from __future__ import annotations

import numpy as np
from hockey_rink import IIHFRink, NHLRink
from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QVBoxLayout, QWidget

from hockey_analyzer.domain.enums import RinkType, ShotOutcome, ShotType

# hockey_rink's default "ice" feature (invisible by default -- it's a
# textured background image, not a line marking) eagerly fetches an image
# over the network the instant a rink is constructed, regardless of
# whether it's ever drawn, and a bug in its own except-fallback
# (`urllib.request`/`urllib.error` referenced but never imported) makes
# that crash outright rather than degrade gracefully -- unacceptable for
# this "local, offline" app (see ADR-0009). Passing a 1x1 in-memory
# placeholder skips the network/file path entirely in hockey_rink's own
# `RinkImage.__init__`; since the feature isn't drawn anyway, nothing is
# visually lost.
_DUMMY_ICE_IMAGE = np.zeros((1, 1, 3), dtype=np.uint8)

_RINK_CLASSES: dict[RinkType, type] = {
    RinkType.IIHF: IIHFRink,
    RinkType.NHL: NHLRink,
}


def build_rink(rink_type: RinkType):
    return _RINK_CLASSES[rink_type](ice={"image": _DUMMY_ICE_IMAGE})


class RinkDiagramWidget(FigureCanvasQTAgg):
    """Emits `location_clicked(x, y)`, in rink-coordinate feet, for a
    click landing on the drawn rink. A click outside the rink's aspect-
    locked drawing area (the letterboxed margin `matplotlib` leaves when
    this widget's own aspect ratio doesn't match the rink's) lands outside
    the `Axes` box entirely and is silently ignored."""

    location_clicked = Signal(float, float)

    def __init__(self, rink_type: RinkType, parent: QWidget | None = None) -> None:
        figure = Figure()
        super().__init__(figure)
        if parent is not None:
            self.setParent(parent)
        self.setMinimumSize(320, 136)

        figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
        self._ax = figure.add_subplot(111)
        build_rink(rink_type).draw(ax=self._ax, display_range="full")

        self.mpl_connect("button_press_event", self._on_click)

    def _on_click(self, event: MouseEvent) -> None:
        if event.xdata is None or event.ydata is None:
            return
        # `event.inaxes` alone isn't a reliable "was this click actually
        # inside the drawn rink" check on HiDPI displays (matplotlib's Qt
        # backend can mis-hit-test at fractional devicePixelRatio scales),
        # so the click is also bounds-checked against the Axes' own data
        # limits -- an equivalent, DPI-independent test, since xdata/ydata
        # already come from the same data transform those limits define.
        x_min, x_max = self._ax.get_xlim()
        y_min, y_max = self._ax.get_ylim()
        if not (x_min <= event.xdata <= x_max and y_min <= event.ydata <= y_max):
            return
        self.location_clicked.emit(float(event.xdata), float(event.ydata))


class RinkClickDialog(QDialog):
    """Modal "click a point" prompt: shows a `RinkDiagramWidget` and
    accepts itself the instant the tagger clicks a location -- there is
    no separate confirm step, matching the log buttons' single-click-to-
    act feel elsewhere in the tagging panel."""

    def __init__(self, rink_type: RinkType, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Click a location")
        self.rink = RinkDiagramWidget(rink_type, self)
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

    def __init__(self, rink_type: RinkType, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shot attempt")
        self.rink = RinkDiagramWidget(rink_type, self)
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
