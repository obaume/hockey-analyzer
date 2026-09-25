"""The clip export dialog (tickets 24, 50) driven through its widgets, over
a hand-built `ClipGame`; the encoder and the preview's media player are
fakes that record what they were asked to do, so nothing here touches
ffmpeg, real footage or real decoding."""

from __future__ import annotations

from pathlib import Path

import pytest
from clip_fixtures import (
    ALICE,
    CARL,
    SECOND,
    ClipGame,
    FakeEncoder,
    FakePreviewPlayer,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QDialog

from hockey_analyzer.domain.clip_export import ClipEncodingError, ClipSegment
from hockey_analyzer.domain.enums import EventType, ShotOutcome
from hockey_analyzer.ui.clip_export_dialog import ClipExportDialog
from hockey_analyzer.ui.shortcuts import PLAYBACK_SCOPE, ShortcutRegistry

FOOTAGE_MS = 3600 * SECOND


@pytest.fixture
def game():
    """Opening period start/faceoff at 0s, an Alice goal at 20s, the
    faceoff restarting play at 25s, and a saved shot at 30s."""
    game = ClipGame()
    game.shot(20, shooter=ALICE, outcome=ShotOutcome.GOAL)
    game.faceoff(25)
    game.shot(30, outcome=ShotOutcome.SAVED)
    return game


def _registry(tagged=None):
    """As the main window leaves it with a game open: its `playback`
    scope active with Space bound there, and a tagging scope whose 7 logs
    a shot attempt (into `tagged`, when given)."""
    registry = ShortcutRegistry()
    registry.enter_scope(PLAYBACK_SCOPE)
    registry.register("Space", PLAYBACK_SCOPE, lambda: None)
    registry.enter_scope("tagging")
    log = tagged if tagged is not None else []
    registry.register("7", "tagging", lambda: log.append("shot_attempt"))
    return registry


def _dialog(qtbot, game, **kw):
    kw.setdefault("footage_duration_ms", FOOTAGE_MS)
    kw.setdefault("encoder", FakeEncoder())
    kw.setdefault("notice", lambda title, text: None)
    kw.setdefault("shortcuts", _registry())
    kw.setdefault("player", FakePreviewPlayer())
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


# -- Clip preview (ticket 50) --------------------------------------------


def _highlight(dialog, row):
    dialog.candidate_list.setCurrentRow(row)


def _placeholder(dialog):
    """The preview's placeholder text, or None while it shows footage."""
    label = dialog.preview.placeholder
    return label.text() if label.isVisibleTo(dialog) else None


def test_the_preview_starts_with_a_select_an_event_placeholder(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)

    assert _placeholder(dialog) == "Select an event to preview"
    assert player.calls == []


def test_highlighting_a_row_loads_its_padding_window_paused_at_the_start(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)

    _highlight(dialog, 2)  # Alice's goal at 20s: 15s-23s with default padding

    assert player.source() == QUrl.fromLocalFile("C:/footage/game.mp4")
    assert player.position() == 15 * SECOND
    assert not player.playing
    assert _placeholder(dialog) is None
    assert dialog.preview.scrubber.minimum() == 15 * SECOND
    assert dialog.preview.scrubber.maximum() == 23 * SECOND


def test_an_unchecked_row_can_still_be_previewed(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _set_checked(dialog, 4, False)

    _highlight(dialog, 4)  # the saved shot at 30s: 25s-33s

    assert player.position() == 25 * SECOND
    assert _placeholder(dialog) is None


def test_toggling_a_checkbox_leaves_the_preview_alone(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)
    player.calls.clear()

    _set_checked(dialog, 4, False)
    _set_checked(dialog, 2, False)

    assert player.calls == []
    assert player.position() == 15 * SECOND


def test_the_arrow_keys_move_to_the_next_and_previous_event(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)

    qtbot.keyClick(dialog.candidate_list, Qt.Key.Key_Down)
    assert player.position() == 20 * SECOND  # the faceoff at 25s
    qtbot.keyClick(dialog.candidate_list, Qt.Key.Key_Up)

    assert player.position() == 15 * SECOND
    assert not player.playing


def test_editing_the_padding_reloads_the_window_at_its_new_start(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)
    player.play()

    dialog.padding_before_spin.setValue(2.0)
    dialog.padding_after_spin.setValue(1.5)

    assert player.position() == 18 * SECOND
    assert not player.playing
    assert dialog.preview.scrubber.minimum() == 18 * SECOND
    assert dialog.preview.scrubber.maximum() == 21500


def test_the_scrubber_marks_the_events_own_moment(qtbot, game):
    dialog = _dialog(qtbot, game)

    _highlight(dialog, 2)

    assert dialog.preview.scrubber.marker_ms == 20 * SECOND


def test_an_event_past_the_end_of_the_footage_shows_why_it_has_no_preview(qtbot, game):
    game.shot(4000)  # footage is an hour long
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)

    _highlight(dialog, 5)

    assert _placeholder(dialog) == (
        "This event is outside the footage — its clip would be empty"
    )
    assert player.calls == []


def test_changing_a_filter_resets_the_preview(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)
    player.play()

    _choose(dialog.event_type_combo, EventType.SHOT_ATTEMPT)

    assert _placeholder(dialog) == "Select an event to preview"
    assert not player.playing
    assert dialog.candidate_list.currentRow() == -1


def test_p_plays_and_pauses_the_preview(qtbot, game):
    player = FakePreviewPlayer()
    shortcuts = _registry()
    dialog = _dialog(qtbot, game, player=player, shortcuts=shortcuts)
    _highlight(dialog, 2)

    assert shortcuts.dispatch("P")
    assert player.playing
    assert dialog.preview.play_button.text() == "Pause"
    shortcuts.dispatch("P")

    assert not player.playing
    assert dialog.preview.play_button.text() == "Play"


def test_p_in_the_candidate_list_plays_rather_than_searching(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)

    qtbot.keyClick(dialog.candidate_list, Qt.Key.Key_P)

    assert player.playing
    assert dialog.candidate_list.currentRow() == 2


def test_space_still_checks_and_unchecks_the_highlighted_row(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)

    qtbot.keyClick(dialog.candidate_list, Qt.Key.Key_Space)

    assert _candidates(dialog)[2][1] is False
    assert not player.playing


def test_the_play_button_plays_the_preview(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    assert not dialog.preview.play_button.isEnabled()
    _highlight(dialog, 2)

    qtbot.mouseClick(dialog.preview.play_button, Qt.MouseButton.LeftButton)

    assert player.playing


def test_playback_pauses_at_the_end_of_the_window(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)
    player.play()

    player.advance_to(23 * SECOND + 40)

    assert not player.playing
    assert player.position() == 23 * SECOND


def test_playing_again_from_the_end_starts_over(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)
    player.play()
    player.advance_to(23 * SECOND)

    dialog.preview.play_button.click()

    assert player.playing
    assert player.position() == 15 * SECOND


def test_scrubbing_stays_within_the_window(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 2)

    dialog.preview.scrubber.setValue(19 * SECOND)
    assert player.position() == 19 * SECOND
    dialog.preview.seek(40 * SECOND)
    assert player.position() == 23 * SECOND
    dialog.preview.seek(0)

    assert player.position() == 15 * SECOND


def test_the_preview_works_for_a_highlight_reel_export_too(qtbot, game):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    dialog.reel_radio.setChecked(True)

    _highlight(dialog, 4)

    assert player.position() == 25 * SECOND


class _ObservingEncoder:
    """Notes what `dialog`'s preview showed when encoding started."""

    def __init__(self, player) -> None:
        self.dialog = None
        self.player = player
        self.seen: list[tuple[str | None, bool]] = []

    def encode(self, source_path, segments, output_path) -> None:
        self.seen.append((_placeholder(self.dialog), self.player.playing))


def test_export_clears_the_preview_before_encoding(qtbot, game, tmp_path):
    player = FakePreviewPlayer()
    encoder = _ObservingEncoder(player)
    dialog = _dialog(qtbot, game, player=player, encoder=encoder)
    encoder.dialog = dialog
    _choose(dialog.player_combo, ALICE)
    _highlight(dialog, 0)
    player.play()

    _export(qtbot, dialog, tmp_path)

    assert encoder.seen == [("Select an event to preview", False)]


@pytest.mark.parametrize("close", ["accept", "reject"])
def test_closing_hands_the_keys_back_to_main_playback(qtbot, game, close):
    main_space = []
    shortcuts = ShortcutRegistry()
    shortcuts.enter_scope(PLAYBACK_SCOPE)
    shortcuts.register("Space", PLAYBACK_SCOPE, lambda: main_space.append(1))
    dialog = _dialog(qtbot, game, shortcuts=shortcuts)
    assert not shortcuts.dispatch("Space")

    getattr(dialog, close)()

    assert not shortcuts.dispatch("P")
    assert shortcuts.dispatch("Space")
    assert main_space == [1]


def test_the_dialog_can_be_opened_again_after_closing(qtbot, game):
    shortcuts = _registry()
    _dialog(qtbot, game, shortcuts=shortcuts).reject()
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, shortcuts=shortcuts, player=player)
    _highlight(dialog, 2)

    shortcuts.dispatch("P")

    assert player.playing


def test_main_window_tagging_keys_do_nothing_under_the_dialog(qtbot, game):
    tagged = []
    dialog = _dialog(qtbot, game, shortcuts=_registry(tagged))
    _highlight(dialog, 2)

    qtbot.keyClick(dialog.candidate_list, Qt.Key.Key_7)
    qtbot.keyClick(dialog, Qt.Key.Key_7)

    assert tagged == []


def test_a_late_position_report_from_the_last_window_doesnt_move_the_new_one(
    qtbot, game
):
    player = FakePreviewPlayer()
    dialog = _dialog(qtbot, game, player=player)
    _highlight(dialog, 4)  # 25s-33s
    player.play()

    _highlight(dialog, 2)  # 15s-23s
    player.calls.clear()
    player.advance_to(30 * SECOND)  # still reporting the old playback

    assert player.calls == []
    assert dialog.preview.scrubber.value() == 15 * SECOND
