"""Game -> Sync Check… (#102): the active game's sync check findings,
grouped by kind. Wiring only -- what the findings are is `test_sync_check`'s
business."""

from __future__ import annotations

from PySide6.QtCore import Qt
from stats_fixtures import HOME, UNKNOWN, GameBuilder

from hockey_analyzer.domain.enums import OfficialEventKind, Side
from hockey_analyzer.domain.tagging_session import OfficialPlayerNotRosteredError
from hockey_analyzer.ui.sync_check_dialog import SyncCheckDialog

SECOND = 1000
KIM_LICENCE = "300014"


def _game(*, official=True):
    """Period 1: an unknown-shooter goal the league gives #14 Kim at 1:40
    (a fillable Disagreement), a goal tagged at 5:00 the league doesn't
    have (Extra), and an official goal at 10:00 nobody tagged (Missing)."""
    game = GameBuilder()
    game.player(HOME, 14, name="Jordan Kim", league_id=KIM_LICENCE)
    game.period_start(1, at=0)
    game.faceoff(0.0, at=0)
    unknown = game.goal(HOME, shooter=UNKNOWN, at=100 * SECOND)
    game.faceoff(0.0, at=100 * SECOND)
    extra = game.goal(HOME, shooter=UNKNOWN, at=300 * SECOND)
    if official:
        kim = game.official(
            OfficialEventKind.GOAL, 1, 100, Side.HOME, player=(KIM_LICENCE, 14)
        )
        missed = game.official(
            OfficialEventKind.GOAL, 1, 600, Side.HOME, player=(KIM_LICENCE, 14)
        )
        kim.player_name = missed.player_name = "Kim Jordan"
    return game.build(), unknown, extra


class _Confirm:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.questions: list[str] = []

    def __call__(self, question: str) -> bool:
        self.questions.append(question)
        return self.answer


class _Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.error = error

    def __call__(self, *args) -> None:
        self.calls.append(args)
        if self.error is not None:
            raise self.error


def _make_dialog(qtbot, data, *, confirm=None, fill=None, notice=None):
    loads = []

    def load():
        loads.append(data)
        return data

    seek = _Recorder()
    dialog = SyncCheckDialog(
        load,
        seek=seek,
        fill=fill or _Recorder(),
        confirm=confirm or _Confirm(True),
        notice=notice or _Recorder(),
    )
    qtbot.addWidget(dialog)
    return dialog, seek, loads


def _groups(dialog):
    tree = dialog.findings_tree
    return {
        tree.topLevelItem(i).text(0): [
            tree.topLevelItem(i).child(j).text(0)
            for j in range(tree.topLevelItem(i).childCount())
        ]
        for i in range(tree.topLevelItemCount())
    }


def _finding_item(dialog, group_prefix):
    tree = dialog.findings_tree
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        if group.text(0).startswith(group_prefix):
            return group.child(0)
    raise AssertionError(f"no {group_prefix} group")


def _click(qtbot, dialog, item):
    tree = dialog.findings_tree
    tree.scrollToItem(item)
    rect = tree.visualItemRect(item)
    qtbot.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_findings_are_listed_grouped_by_kind(qtbot):
    data, _, _ = _game()
    dialog, _, _ = _make_dialog(qtbot, data)

    groups = _groups(dialog)

    assert list(groups) == ["Missing (1)", "Extra (1)", "Disagreement (1)"]
    assert groups["Missing (1)"] == ["P1 10:00  Goal, home: #14 Kim Jordan"]
    assert groups["Extra (1)"] == [
        "P1 5:00  Goal, home: tagged, not in the league's record"
    ]
    assert groups["Disagreement (1)"] == [
        "P1 1:40  Goal, home: shooter is unknown, the league has #14 Kim Jordan"
    ]
    assert dialog.message_label.isHidden()


def test_clicking_a_finding_seeks_to_its_tagged_event(qtbot):
    data, _, extra = _game()
    dialog, seek, _ = _make_dialog(qtbot, data)

    _click(qtbot, dialog, _finding_item(dialog, "Extra"))

    assert seek.calls == [(extra.video_timestamp,)]


def test_clicking_a_missing_goal_seeks_nowhere(qtbot):
    data, _, _ = _game()
    dialog, seek, _ = _make_dialog(qtbot, data)

    _click(qtbot, dialog, _finding_item(dialog, "Missing"))

    assert seek.calls == []


def test_fill_is_offered_only_on_a_fillable_disagreement(qtbot):
    data, _, _ = _game()
    dialog, _, _ = _make_dialog(qtbot, data)

    _click(qtbot, dialog, _finding_item(dialog, "Extra"))
    assert dialog.fill_button.isEnabled() is False

    _click(qtbot, dialog, _finding_item(dialog, "Disagreement"))
    assert dialog.fill_button.isEnabled() is True


def test_fill_asks_for_confirmation_then_fills_that_field(qtbot):
    data, unknown, _ = _game()
    confirm, fill = _Confirm(True), _Recorder()
    dialog, _, loads = _make_dialog(qtbot, data, confirm=confirm, fill=fill)
    _click(qtbot, dialog, _finding_item(dialog, "Disagreement"))

    qtbot.mouseClick(dialog.fill_button, Qt.MouseButton.LeftButton)

    assert confirm.questions == [
        "Set the shooter of this goal to #14 Kim Jordan, as the league has it?"
    ]
    assert fill.calls == [(unknown.id, "shooter", KIM_LICENCE)]
    # Re-checked after the fill.
    assert len(loads) == 2


def test_declining_the_confirmation_fills_nothing(qtbot):
    data, _, _ = _game()
    fill = _Recorder()
    dialog, _, loads = _make_dialog(qtbot, data, confirm=_Confirm(False), fill=fill)
    _click(qtbot, dialog, _finding_item(dialog, "Disagreement"))

    qtbot.mouseClick(dialog.fill_button, Qt.MouseButton.LeftButton)

    assert fill.calls == []
    assert len(loads) == 1


def test_a_refused_fill_says_why(qtbot):
    data, _, _ = _game()
    notice = _Recorder()
    fill = _Recorder(OfficialPlayerNotRosteredError("#14 isn't on the roster"))
    dialog, _, _ = _make_dialog(qtbot, data, fill=fill, notice=notice)
    _click(qtbot, dialog, _finding_item(dialog, "Disagreement"))

    qtbot.mouseClick(dialog.fill_button, Qt.MouseButton.LeftButton)

    assert notice.calls == [("#14 isn't on the roster",)]


def test_a_game_without_official_events_says_so(qtbot):
    data, _, _ = _game(official=False)
    dialog, _, _ = _make_dialog(qtbot, data)

    assert not dialog.message_label.isHidden()
    assert "no Official events" in dialog.message_label.text()
    assert dialog.findings_tree.isHidden()


def test_a_clean_game_says_so(qtbot):
    game = GameBuilder()
    game.official(OfficialEventKind.GOALIE_CHANGE, 1, 100, Side.HOME)
    dialog, _, _ = _make_dialog(qtbot, game.build())

    assert "No findings" in dialog.message_label.text()
    assert not dialog.message_label.isHidden()
