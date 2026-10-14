"""The on-ice dock: each side's On-ice set (see CONTEXT.md) at the current
video position, so the tagger can see who can take a faceoff, a shot or a
change. Read-only -- it shows the shift log, it never writes to it.

The game is snapshotted once per `reload` (whenever the tagging panel's
events change) and each playback position is derived from that snapshot
in memory, since positions arrive many times a second during playback.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.enums import Side
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.on_ice import OnIcePlayer, OnIceSet, on_ice_sets
from hockey_analyzer.domain.tagging_session import TaggingSession

_SIDES = (Side.HOME, Side.AWAY)
_SIDE_NAMES = {Side.HOME: "Home", Side.AWAY: "Away"}
_COLUMNS = ("#", "Name", "Pos", "Shift")
# What the dock's minimum width is measured against (see
# `_TeamView._columns_min_width`).
_WIDEST_ROW = ("#99", "Firstname Lastname", "RW", "19:59")
# Translucent, so it reads on light and dark palettes alike.
_LONG_SHIFT_BRUSH = QBrush(QColor(255, 170, 0, 110))


class OnIceDock(QDockWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("On Ice", parent)
        # saveState/restoreState key; also how tests find it.
        self.setObjectName("on_ice_dock")
        # Hidden only through the View menu / `I` toggle, which is what
        # remembers the choice -- a title-bar close button would bypass it.
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self._session: TaggingSession | None = None
        self._data: GameData | None = None
        self._position_ms = 0

        self.tabs = QTabWidget()
        # Never steals the keyboard from the main window's hotkeys.
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.team_views = {side: _TeamView() for side in _SIDES}
        for side in _SIDES:
            self.tabs.addTab(self.team_views[side], _SIDE_NAMES[side])
        self.setWidget(self.tabs)
        self._show()

    def set_session(self, session: TaggingSession | None) -> None:
        self._session = session
        self.reload()

    def reload(self) -> None:
        """Re-reads the game -- call whenever its events change."""
        self._data = self._session.game_data() if self._session is not None else None
        self._show()

    def set_position(self, position_ms: int) -> None:
        self._position_ms = position_ms
        self._show()

    def flip_tab(self) -> None:
        self.tabs.setCurrentIndex(1 - self.tabs.currentIndex())

    def _show(self) -> None:
        sets = (
            on_ice_sets(self._data, self._position_ms)
            if self._data is not None and self._session is not None
            else {}
        )
        for index, side in enumerate(_SIDES):
            team_id = (
                None
                if self._session is None
                else self._session.home_team_id
                if side is Side.HOME
                else self._session.away_team_id
            )
            on_ice = sets.get(team_id, OnIceSet())
            self.team_views[side].show_set(on_ice)
            self.tabs.setTabText(index, f"{_SIDE_NAMES[side]} {_count(on_ice)}")


class _TeamView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.header = QLabel()
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Name takes whatever width the narrow #/Pos/Shift columns leave.
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumWidth(self._columns_min_width())
        self.goalie_label = QLabel()
        self.unresolved_label = QLabel()

        layout = QVBoxLayout()
        layout.addWidget(self.header)
        layout.addWidget(self.table, stretch=1)
        layout.addWidget(self.goalie_label)
        layout.addWidget(self.unresolved_label)
        self.setLayout(layout)

    def _columns_min_width(self) -> int:
        """Wide enough for every column at its usual widest (bold, as a long
        shift is), so a tight window takes its room from the panel beside
        the dock instead of hiding the Shift column. Fixed rather than
        fitted to whoever is on, so the dock doesn't jump as players
        change; a longer name is elided."""
        self.table.setRowCount(1)
        for column, text in enumerate(_WIDEST_ROW):
            item = QTableWidgetItem(text)
            font = QFont(item.font())
            font.setBold(True)
            item.setFont(font)
            self.table.setItem(0, column, item)
        header = self.table.horizontalHeader()
        columns = sum(
            max(self.table.sizeHintForColumn(column), header.sectionSizeHint(column))
            for column in range(len(_COLUMNS))
        )
        self.table.setRowCount(0)
        return (
            columns
            + 2 * self.table.frameWidth()
            + self.table.verticalScrollBar().sizeHint().width()
        )

    def show_set(self, on_ice: OnIceSet) -> None:
        self.header.setText(_header(on_ice))
        self.table.setRowCount(len(on_ice.skaters))
        for row, player in enumerate(on_ice.skaters):
            cells = (
                _jersey(player),
                player.full_name or "",
                player.position.value if player.position is not None else "",
                _format_shift(player.shift_ms),
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if player.long_shift:
                    item.setBackground(_LONG_SHIFT_BRUSH)
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, column, item)
        self.goalie_label.setText(_goalie_line(on_ice.goalies))
        unresolved = on_ice.unresolved_shift_changes
        self.unresolved_label.setText(
            f"{unresolved} unresolved shift change{'s' if unresolved != 1 else ''}"
            if unresolved
            else ""
        )
        self.unresolved_label.setVisible(bool(unresolved))


def _count(on_ice: OnIceSet) -> str:
    """The tab label's summary: skaters, then "+G" per goalie in net."""
    goalies = len(on_ice.goalies)
    suffix = "" if not goalies else "+G" if goalies == 1 else f"+{goalies}G"
    return f"{len(on_ice.skaters)}{suffix}"


def _header(on_ice: OnIceSet) -> str:
    skaters = len(on_ice.skaters)
    noun = "skater" if skaters == 1 else "skaters"
    goalies = len(on_ice.goalies)
    if not goalies:
        return f"{skaters} {noun}, no goalie"
    return f"{skaters} {noun} + {'G' if goalies == 1 else f'{goalies} G'}"


def _goalie_line(goalies: Sequence[OnIcePlayer]) -> str:
    if not goalies:
        return "G  —"
    return "G  " + ", ".join(
        " ".join(part for part in (_jersey(goalie), goalie.full_name) if part)
        for goalie in goalies
    )


def _jersey(player: OnIcePlayer) -> str:
    return f"#{player.jersey_number}" if player.jersey_number is not None else "#?"


def _format_shift(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"
