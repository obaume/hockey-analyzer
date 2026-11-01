"""Game -> Sync Check… (#102): the active game's tagging against its
Official events (see CONTEXT.md's Sync check), findings grouped by kind.
Clicking a finding seeks the footage to its tagged event; a Disagreement
whose tagged player is unknown can be filled from the official one, after
a yes/no confirmation, one field at a time.

The dialog only reads and asks: the check itself is `sync_check`, and a
fill goes through the injected `fill` (the main window's
`TaggingSession.fill_from_official`), after which the findings are
re-checked.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hockey_analyzer.domain.enums import OfficialEventKind, Side
from hockey_analyzer.domain.game_data import GameData
from hockey_analyzer.domain.models import Event, OfficialEvent, OfficialPlayer, Penalty
from hockey_analyzer.domain.sync_check import Finding, FindingKind, sync_check
from hockey_analyzer.domain.tagging_session import (
    REFERENCE_NAMES,
    NamedPlayerNotOverwrittenError,
    OfficialPlayerNotRosteredError,
)
from hockey_analyzer.ui.game_setup_dialog import Confirm

# Sets one unknown player reference: (event id, reference name, licence
# number) -- `TaggingSession.fill_from_official`'s shape.
Fill = Callable[[int, str, str], object]

NO_OFFICIAL_EVENTS = (
    "This game has no Official events: it wasn't imported from a league "
    "link, or hasn't been refreshed from the league yet. Use Game > "
    "Refresh from League to fetch them."
)
NO_FINDINGS = "No findings: tagged goals and penalties agree with the league's."

_GROUP_NAMES = {
    FindingKind.MISSING: "Missing",
    FindingKind.EXTRA: "Extra",
    FindingKind.DRIFT: "Drift",
    FindingKind.DISAGREEMENT: "Disagreement",
}
_KIND_NAMES = {OfficialEventKind.GOAL: "Goal", OfficialEventKind.PENALTY: "Penalty"}
# What a slot on the Official event a Disagreement's field compares with.
_OFFICIAL_SLOTS = {
    "shooter": "player",
    "assist1": "assist1",
    "assist2": "assist2",
    "player": "player",
}


class SyncCheckDialog(QDialog):
    def __init__(
        self,
        load: Callable[[], GameData],
        *,
        seek: Callable[[int], None],
        fill: Fill | None = None,
        confirm: Confirm | None = None,
        notice: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sync Check")
        self._load = load
        self._seek = seek
        self._fill = fill
        self._confirm = confirm or self._ask
        self._notice = notice or self._warn
        self._data: GameData | None = None

        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.findings_tree = QTreeWidget()
        self.findings_tree.setHeaderHidden(True)
        self.findings_tree.itemClicked.connect(self._on_item_clicked)
        self.findings_tree.currentItemChanged.connect(self._refresh_fill_button)

        self.fill_button = QPushButton("Fill from Official")
        self.fill_button.setEnabled(False)
        self.fill_button.clicked.connect(self._fill_selected)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(self.fill_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addWidget(self.message_label)
        layout.addWidget(self.findings_tree)
        layout.addLayout(buttons)
        self.setLayout(layout)
        self.resize(640, 420)
        self._reload()

    def _reload(self) -> None:
        self._data = self._load()
        result = sync_check(self._data)
        self.findings_tree.clear()
        for kind in FindingKind:
            findings = result.of_kind(kind)
            if not findings:
                continue
            group = QTreeWidgetItem([f"{_GROUP_NAMES[kind]} ({len(findings)})"])
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for finding in findings:
                item = QTreeWidgetItem([_finding_text(self._data, finding)])
                item.setData(0, Qt.ItemDataRole.UserRole, finding)
                group.addChild(item)
            self.findings_tree.addTopLevelItem(group)
            group.setExpanded(True)
        if not result.has_official_events:
            self.message_label.setText(NO_OFFICIAL_EVENTS)
        elif not result.findings:
            self.message_label.setText(NO_FINDINGS)
        self.message_label.setHidden(bool(result.findings))
        self.findings_tree.setHidden(not result.findings)
        self._refresh_fill_button()

    def _selected(self) -> Finding | None:
        item = self.findings_tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def _refresh_fill_button(self, *_) -> None:
        finding = self._selected()
        self.fill_button.setEnabled(
            self._fill is not None and finding is not None and finding.fillable
        )

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        finding = item.data(0, Qt.ItemDataRole.UserRole)
        if finding is None or finding.tagged_event_id is None:
            return
        event = _event(self._data, finding.tagged_event_id)
        if event is not None:
            self._seek(event.video_timestamp)

    def _fill_selected(self) -> None:
        finding = self._selected()
        if finding is None or not finding.fillable or self._fill is None:
            return
        official = finding.official.official_player(_OFFICIAL_SLOTS[finding.field])
        kind = _KIND_NAMES[finding.official.kind].lower()
        question = (
            f"Set the {REFERENCE_NAMES[finding.field]} of this {kind} to "
            f"{_official_label(official)}, as the league has it?"
        )
        if not self._confirm(question):
            return
        try:
            self._fill(finding.tagged_event_id, finding.field, official.league_id)
        except (
            NamedPlayerNotOverwrittenError,
            OfficialPlayerNotRosteredError,
        ) as error:
            self._notice(str(error))
            return
        self._reload()

    def _ask(self, question: str) -> bool:
        answer = QMessageBox.question(self, "Fill from Official", question)
        return answer == QMessageBox.StandardButton.Yes

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "Fill from Official", message)


def _event(data: GameData, event_id: int) -> Event | None:
    return next((event for event in data.events if event.id == event_id), None)


def _clock(period: int | None, elapsed_ms: int | None) -> str:
    """e.g. "P2 8:22" -- elapsed time in the period, as the league
    counts it."""
    if period is None or elapsed_ms is None:
        return "Before the first period"
    return f"P{period} {_minutes(elapsed_ms)}"


def _minutes(elapsed_ms: int) -> str:
    seconds = elapsed_ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _official_label(player: OfficialPlayer | None) -> str:
    if player is None:
        return "nobody"
    jersey = f"#{player.jersey}" if player.jersey is not None else ""
    return " ".join(part for part in (jersey, player.name) if part) or (
        f"licence {player.league_id}"
    )


def _what(kind: OfficialEventKind, side: Side | None) -> str:
    return f"{_KIND_NAMES[kind]}, {side.value}" if side else _KIND_NAMES[kind]


def _tagged_label(data: GameData, event: Event, column: str) -> str:
    if getattr(event, f"{column}_unknown"):
        return "unknown"
    player_id = getattr(event, f"{column}_id")
    if player_id is None:
        return "not set"
    entry = next((e for e in data.roster if e.player_id == player_id), None)
    if entry is None:
        return f"player {player_id}"
    name = entry.player.full_name if entry.player is not None else None
    return f"#{entry.jersey_number} {name}" if name else f"#{entry.jersey_number}"


_COLUMNS = {
    ("shooter", OfficialEventKind.GOAL): "shooter",
    ("assist1", OfficialEventKind.GOAL): "assist1",
    ("assist2", OfficialEventKind.GOAL): "assist2",
    ("player", OfficialEventKind.PENALTY): "penalty_player",
}


def _finding_text(data: GameData, finding: Finding) -> str:
    official: OfficialEvent | None = finding.official
    if finding.kind is FindingKind.MISSING:
        when = _clock(official.period_number, official.elapsed_seconds * 1000)
        who = _official_label(official.official_player("player"))
        detail = (
            f" ({official.penalty_minutes} min {official.penalty_name})"
            if official.kind is OfficialEventKind.PENALTY and official.penalty_name
            else ""
        )
        return f"{when}  {_what(official.kind, official.side)}: {who}{detail}"
    when = _clock(finding.period, finding.tagged_elapsed_ms)
    if finding.kind is FindingKind.DRIFT:
        seconds = round(finding.drift_ms / 1000)
        direction = "ahead of" if seconds > 0 else "behind"
        return (
            f"P{finding.period}  the tagged clock drifts {abs(seconds)} s "
            f"{direction} the league's across the period -- a missed "
            f"stoppage? Look before {_minutes(finding.tagged_elapsed_ms)}"
        )
    event = _event(data, finding.tagged_event_id)
    if finding.kind is FindingKind.EXTRA:
        kind = (
            OfficialEventKind.PENALTY
            if isinstance(event, Penalty)
            else OfficialEventKind.GOAL
        )
        side = _event_side(data, event)
        return f"{when}  {_what(kind, side)}: tagged, not in the league's record"
    what = _what(official.kind, official.side)
    if finding.field == "strength":
        return (
            f"{when}  {what}: strength is {event.strength_state}, "
            f"the league has {official.strength_tag}"
        )
    tagged = _tagged_label(data, event, _COLUMNS[finding.field, official.kind])
    league = _official_label(official.official_player(_OFFICIAL_SLOTS[finding.field]))
    return (
        f"{when}  {what}: {REFERENCE_NAMES[finding.field]} is {tagged}, "
        f"the league has {league}"
    )


def _event_side(data: GameData, event: Event | None) -> Side | None:
    team_id = getattr(event, "shot_team_id", None) or getattr(
        event, "penalty_team_id", None
    )
    if team_id is None:
        return None
    if team_id == data.game.home_team_id:
        return Side.HOME
    return Side.AWAY if team_id == data.game.away_team_id else None
