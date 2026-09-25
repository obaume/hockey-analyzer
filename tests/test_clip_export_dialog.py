"""The clip export dialog (ticket 24) driven through its widgets, over a
hand-built `ClipGame`; the encoder is a fake that records what it was
asked to write, so nothing here touches ffmpeg or real footage."""

from __future__ import annotations

from pathlib import Path

import pytest
from clip_fixtures import ALICE, CARL, SECOND, ClipGame
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from hockey_analyzer.domain.clip_export import ClipEncodingError, ClipSegment
from hockey_analyzer.domain.enums import EventType, ShotOutcome
from hockey_analyzer.ui.clip_export_dialog import ClipExportDialog

FOOTAGE_MS = 3600 * SECOND


class FakeEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list, Path]] = []

    def encode(self, source_path, segments, output_path) -> None:
        self.calls.append((source_path, list(segments), output_path))


@pytest.fixture
def game():
    """Opening period start/faceoff at 0s, an Alice goal at 20s, the
    faceoff restarting play at 25s, and a saved shot at 30s."""
    game = ClipGame()
    game.shot(20, shooter=ALICE, outcome=ShotOutcome.GOAL)
    game.faceoff(25)
    game.shot(30, outcome=ShotOutcome.SAVED)
    return game


def _dialog(qtbot, game, **kw):
    kw.setdefault("footage_duration_ms", FOOTAGE_MS)
    kw.setdefault("encoder", FakeEncoder())
    kw.setdefault("notice", lambda title, text: None)
    dialog = ClipExportDialog(game.data(), **kw)
    qtbot.addWidget(dialog)
    return dialog


def _candidates(dialog):
    items = [
        dialog.candidate_list.item(i) for i in range(dialog.candidate_list.count())
    ]
    return [(item.text(), item.checkState() == Qt.CheckState.Checked) for item in items]


def test_every_event_is_a_checked_candidate_before_any_filter(qtbot, game):
    dialog = _dialog(qtbot, game)

    candidates = _candidates(dialog)

    assert len(candidates) == 5
    assert all(checked for _text, checked in candidates)
    assert dialog.export_button.isEnabled()


def test_a_candidate_reads_as_game_clock_event_and_players(qtbot, game):
    dialog = _dialog(qtbot, game)

    text, _checked = _candidates(dialog)[2]

    assert text == "P1 19:40 · Shot attempt (goal) · #9 Alice Müller"


def _choose(combo, data):
    index = combo.findData(data)
    assert index >= 0, f"{data!r} isn't offered"
    combo.setCurrentIndex(index)


def test_player_filter_narrows_the_candidates(qtbot, game):
    dialog = _dialog(qtbot, game)

    _choose(dialog.player_combo, ALICE)

    assert [text for text, _ in _candidates(dialog)] == [
        "P1 19:40 · Shot attempt (goal) · #9 Alice Müller"
    ]


def test_event_type_and_outcome_filters_narrow_the_candidates(qtbot, game):
    dialog = _dialog(qtbot, game)

    _choose(dialog.event_type_combo, EventType.SHOT_ATTEMPT)
    assert len(_candidates(dialog)) == 2
    _choose(dialog.outcome_combo, ShotOutcome.SAVED)

    assert [text for text, _ in _candidates(dialog)] == [
        "P1 19:35 · Shot attempt (saved)"
    ]


def test_players_are_offered_from_this_games_roster(qtbot, game):
    dialog = _dialog(qtbot, game)

    offered = [
        dialog.player_combo.itemText(i) for i in range(dialog.player_combo.count())
    ]

    assert offered == [
        "Any player",
        "#9 Alice Müller (Ice Breakers)",
        "#17 Bob Smith (Ice Breakers)",
        "#4 (Rivals HC)",
    ]


def _set_checked(dialog, row, checked):
    dialog.candidate_list.item(row).setCheckState(
        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    )


def test_zero_matches_disables_export(qtbot, game):
    dialog = _dialog(qtbot, game)

    _choose(dialog.player_combo, CARL)

    assert _candidates(dialog) == []
    assert not dialog.export_button.isEnabled()
    assert dialog.summary_label.text() == "No events match this filter."


def test_unchecking_a_candidate_leaves_it_out_of_the_export(qtbot, game):
    dialog = _dialog(qtbot, game)

    _set_checked(dialog, 0, False)

    assert dialog.summary_label.text() == "4 of 5 events selected."
    assert dialog.export_button.isEnabled()


def test_unchecking_every_candidate_disables_export_until_one_is_rechecked(qtbot, game):
    dialog = _dialog(qtbot, game)
    _choose(dialog.player_combo, ALICE)

    _set_checked(dialog, 0, False)
    assert not dialog.export_button.isEnabled()
    _set_checked(dialog, 0, True)

    assert dialog.export_button.isEnabled()


def _export(qtbot, dialog, output_dir):
    dialog.output_dir_edit.setText(str(output_dir))
    qtbot.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)


def test_padding_is_prefilled_with_the_default(qtbot, game):
    dialog = _dialog(qtbot, game)

    assert dialog.padding_before_spin.value() == 5.0
    assert dialog.padding_after_spin.value() == 3.0


def test_export_writes_one_file_per_selected_clip(qtbot, game, tmp_path):
    encoder = FakeEncoder()
    dialog = _dialog(qtbot, game, encoder=encoder)
    _choose(dialog.player_combo, ALICE)

    _export(qtbot, dialog, tmp_path)

    assert encoder.calls == [
        (
            "C:/footage/game.mp4",
            [ClipSegment(3, 15 * SECOND, 23 * SECOND)],
            tmp_path / "shot_attempt_alice-müller_P1-19m40s.mp4",
        )
    ]


def test_export_uses_the_padding_the_user_set(qtbot, game, tmp_path):
    encoder = FakeEncoder()
    dialog = _dialog(qtbot, game, encoder=encoder)
    _choose(dialog.player_combo, ALICE)
    dialog.padding_before_spin.setValue(2.0)
    dialog.padding_after_spin.setValue(1.5)

    _export(qtbot, dialog, tmp_path)

    (_source, segments, _path) = encoder.calls[0]
    assert segments == [ClipSegment(3, 18 * SECOND, 21500)]


def test_export_as_a_reel_writes_every_selected_clip_to_one_file(qtbot, game, tmp_path):
    encoder = FakeEncoder()
    dialog = _dialog(qtbot, game, encoder=encoder)
    _choose(dialog.event_type_combo, EventType.SHOT_ATTEMPT)
    dialog.reel_radio.setChecked(True)

    _export(qtbot, dialog, tmp_path)

    assert encoder.calls == [
        (
            "C:/footage/game.mp4",
            [
                ClipSegment(3, 15 * SECOND, 23 * SECOND),
                ClipSegment(5, 25 * SECOND, 33 * SECOND),
            ],
            tmp_path / "2026-09-20_vs-rivals-hc_shot_attempt_reel.mp4",
        )
    ]


def test_one_file_per_clip_is_the_default_shape(qtbot, game):
    dialog = _dialog(qtbot, game)

    assert dialog.per_clip_radio.isChecked()
    assert not dialog.reel_radio.isChecked()


class FailingEncoder:
    def encode(self, source_path, segments, output_path) -> None:
        raise ClipEncodingError("Invalid data found when processing input")


def test_a_finished_export_says_where_the_files_went_and_closes(qtbot, game, tmp_path):
    notices = []
    dialog = _dialog(qtbot, game, notice=lambda *n: notices.append(n))
    _choose(dialog.event_type_combo, EventType.SHOT_ATTEMPT)

    _export(qtbot, dialog, tmp_path)

    assert notices == [("Clips exported", f"Exported 2 files to\n{tmp_path}")]
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_a_failed_export_shows_ffmpegs_error_and_stays_open(qtbot, game, tmp_path):
    notices = []
    dialog = _dialog(
        qtbot, game, encoder=FailingEncoder(), notice=lambda *n: notices.append(n)
    )

    _export(qtbot, dialog, tmp_path)

    ((title, text),) = notices
    assert title == "Export failed"
    assert "Invalid data found when processing input" in text
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_the_output_folder_defaults_next_to_the_footage(qtbot, game):
    dialog = _dialog(qtbot, game)

    assert Path(dialog.output_dir_edit.text()) == Path("C:/footage/clips")


def test_browse_picks_the_output_folder(qtbot, game, tmp_path):
    dialog = _dialog(qtbot, game, choose_directory=lambda start: str(tmp_path))

    qtbot.mouseClick(dialog.browse_button, Qt.MouseButton.LeftButton)

    assert dialog.output_dir_edit.text() == str(tmp_path)


def test_a_cancelled_browse_keeps_the_output_folder(qtbot, game):
    dialog = _dialog(qtbot, game, choose_directory=lambda start: "")

    qtbot.mouseClick(dialog.browse_button, Qt.MouseButton.LeftButton)

    assert Path(dialog.output_dir_edit.text()) == Path("C:/footage/clips")


def test_no_output_folder_disables_export(qtbot, game):
    dialog = _dialog(qtbot, game)

    dialog.output_dir_edit.setText("  ")

    assert not dialog.export_button.isEnabled()


def test_a_roster_entry_whose_player_is_missing_is_listed_by_jersey(qtbot, game):
    """A roster entry can reference a player row that no longer exists
    (SQLite doesn't enforce the foreign key); that shouldn't stop the
    dialog from opening."""
    game.roster[0].player = None

    dialog = _dialog(qtbot, game)

    assert dialog.player_combo.itemText(1) == "#9 (Ice Breakers)"
    assert _candidates(dialog)[2][0] == "P1 19:40 · Shot attempt (goal) · #9"
