"""Undo/redo of tagging actions (#64): one linear history per
`TaggingSession`, one step per tagging action however many saves it made."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from hockey_analyzer.domain.enums import (
    EventType,
    ShotOutcome,
    ShotType,
    Side,
    UnitType,
)
from hockey_analyzer.domain.game_setup import GameSetupService
from hockey_analyzer.domain.models import Game

_TABLES = ("events", "players", "game_roster_entries", "game_unit_assignments")


def _rows(session) -> dict[str, list[dict]]:
    """Every tagging-relevant row, column for column -- what "the database
    is exactly as before" compares. `games` is left out: undo and redo
    bump `Game.updated_at` like any other save."""
    return {
        table: [
            dict(row)
            for row in session.execute(
                text(f"SELECT * FROM {table} ORDER BY id")
            ).mappings()
        ]
        for table in _TABLES
    }


def test_undoing_a_logged_event_removes_it_and_redo_brings_it_back(
    tagging_session, session
):
    before = _rows(session)
    event = tagging_session.log_event(EventType.STOPPAGE, 612)
    logged = _rows(session)

    tagging_session.undo()
    assert _rows(session) == before

    tagging_session.redo()
    assert _rows(session) == logged
    assert tagging_session.get_event(event.id).video_timestamp == 612


@pytest.mark.parametrize(
    ("event_type", "kwargs"),
    [
        (EventType.PERIOD_START, {}),
        (EventType.PERIOD_END, {}),
        (EventType.STOPPAGE, {}),
        (EventType.FACEOFF, {"location": (40.0, 5.0)}),
        (
            EventType.SHOT_ATTEMPT,
            {
                "shot_outcome": ShotOutcome.GOAL,
                "shot_type": ShotType.WRIST,
                "location": (85.0, -3.0),
            },
        ),
        (EventType.PENALTY, {}),
        (EventType.SHIFT_CHANGE, {}),
    ],
)
def test_logging_any_event_type_is_one_step(
    tagging_session, session, event_type, kwargs
):
    before = _rows(session)
    tagging_session.log_event(event_type, 754_000, **kwargs)
    logged = _rows(session)

    tagging_session.undo()
    assert _rows(session) == before
    assert not tagging_session.can_undo

    tagging_session.redo()
    assert _rows(session) == logged


def test_one_undo_removes_a_period_start_and_its_faceoff(tagging_session, session):
    tagging_session.log_period_start(0)
    assert len(tagging_session.list_events()) == 2

    tagging_session.undo()

    assert tagging_session.list_events() == []


def test_a_shot_attempt_is_logged_with_its_location(tagging_session):
    shot = tagging_session.log_event(
        EventType.SHOT_ATTEMPT,
        0,
        shot_outcome=ShotOutcome.SAVED,
        shot_type=ShotType.SLAP,
        location=(85.0, -3.0),
    )

    assert (shot.shot_x, shot.shot_y) == (85.0, -3.0)


def test_undoing_a_delete_restores_the_same_row_in_the_same_place(
    tagging_session, session
):
    period_start, faceoff = tagging_session.log_period_start(60_000)
    tagging_session.update_event(period_start.id, period_number=2)
    before = _rows(session)

    tagging_session.delete_event(period_start.id)
    tagging_session.undo()

    assert _rows(session) == before
    assert [event.id for event in tagging_session.list_events()] == [
        period_start.id,
        faceoff.id,
    ]


def test_undoing_a_jersey_not_yet_rostered_removes_the_new_player_and_entry(
    tagging_session, session
):
    shot = tagging_session.log_event(
        EventType.SHOT_ATTEMPT,
        0,
        shot_outcome=ShotOutcome.GOAL,
        shot_type=ShotType.WRIST,
    )
    tagging_session.set_player_reference(
        shot.id, "home", reference="shooter", unknown=True
    )
    before = _rows(session)

    tagging_session.set_player_reference(
        shot.id, "home", reference="shooter", jersey_number=14
    )
    assert len(_rows(session)["players"]) == 1
    tagging_session.undo()

    assert _rows(session) == before
    restored = tagging_session.get_event(shot.id)
    assert restored.shooter_id is None
    assert restored.shooter_unknown is True


def test_one_undo_removes_a_whole_line_change(tagging_session, session):
    for jersey in (14, 17, 23):
        entry = tagging_session.resolve_or_create_roster_entry(
            tagging_session.home_team_id, jersey
        )
        GameSetupService(session).assign_unit(
            game_id=tagging_session.game_id,
            team_id=tagging_session.home_team_id,
            player_id=entry.player_id,
            unit_type=UnitType.FORWARD_LINE,
            unit_number=1,
        )
    (unit,) = tagging_session.list_units("home")
    before = _rows(session)

    changes = tagging_session.log_unit_change(unit, 5_000, on_ice=True)
    assert len(changes) == 3
    tagging_session.undo()

    assert _rows(session) == before


def test_one_undo_removes_an_ad_hoc_line_change_and_the_jerseys_it_rostered(
    tagging_session, session
):
    tagging_session.resolve_or_create_roster_entry(tagging_session.away_team_id, 9)
    before = _rows(session)

    tagging_session.log_ad_hoc_line_change("away", [9, 91], 5_000, on_ice=True)
    tagging_session.undo()

    assert _rows(session) == before


def test_undoing_several_steps_then_redoing_them_all_round_trips(
    tagging_session, session
):
    stoppage = tagging_session.log_event(EventType.STOPPAGE, 1_000)
    penalty = tagging_session.log_event(EventType.PENALTY, 2_000)
    tagging_session.set_player_reference(penalty.id, "away", jersey_number=22)
    tagging_session.update_event(penalty.id, penalty_infraction="Tripping")
    tagging_session.delete_event(stoppage.id)
    final = _rows(session)

    for _ in range(5):
        tagging_session.undo()
    assert _rows(session)["events"] == []
    for _ in range(5):
        tagging_session.redo()

    assert _rows(session) == final
    assert not tagging_session.can_redo


def test_a_new_action_after_an_undo_makes_redo_unavailable(tagging_session):
    tagging_session.log_event(EventType.STOPPAGE, 1_000)
    tagging_session.undo()
    assert tagging_session.can_redo

    tagging_session.log_event(EventType.PERIOD_END, 2_000)

    assert not tagging_session.can_redo
    assert tagging_session.redo() is None


def test_an_edit_that_changes_nothing_records_no_step(tagging_session):
    event = tagging_session.log_event(EventType.STOPPAGE, 0, strength_state="5v5")
    tagging_session.undo()
    tagging_session.redo()

    tagging_session.update_event(event.id, strength_state="5v5")

    assert tagging_session.last_step is None
    assert tagging_session.undo_description == "Logged Stoppage at 0:00:00"


def test_steps_describe_what_they_did(tagging_session):
    faceoff = tagging_session.log_event(EventType.FACEOFF, 754_000)
    assert tagging_session.undo_description == "Logged Faceoff at 0:12:34"

    tagging_session.update_event(faceoff.id, faceoff_winner=Side.HOME)
    assert tagging_session.undo_description == "Set winner: home"

    tagging_session.update_event(faceoff.id, faceoff_x=40.0, faceoff_y=5.0)
    assert tagging_session.undo_description == "Set location: (40.0, 5.0)"

    tagging_session.set_player_reference(
        faceoff.id, "home", jersey_number=14, full_name="Jordan Kim"
    )
    assert tagging_session.undo_description == "Set home participant: #14 Jordan Kim"

    tagging_session.delete_event(faceoff.id)
    assert tagging_session.undo_description == "Deleted Faceoff at 0:12:34"

    tagging_session.undo()
    assert tagging_session.redo_description == "Deleted Faceoff at 0:12:34"


def test_a_line_change_is_described_by_who_changed(tagging_session):
    tagging_session.log_ad_hoc_line_change("away", [9, 91], 0, on_ice=False)

    assert tagging_session.undo_description == "Line change: #9, #91 OFF"


def test_undo_and_redo_return_the_step_and_its_event(tagging_session):
    _, faceoff = tagging_session.log_period_start(0)

    step = tagging_session.undo()

    assert (
        step.description == "Logged Period start at 0:00:00 and its center-ice faceoff"
    )
    assert step.event_id == faceoff.id
    assert tagging_session.redo() == step


def test_with_nothing_to_undo_undo_does_nothing(tagging_session):
    assert not tagging_session.can_undo
    assert tagging_session.undo_description is None
    assert tagging_session.undo() is None


def test_a_failed_action_records_no_step(tagging_session):
    event = tagging_session.log_event(EventType.STOPPAGE, 0)

    with pytest.raises(ValueError):
        tagging_session.update_event(event.id, not_a_real_field=1)

    assert tagging_session.undo_description == "Logged Stoppage at 0:00:00"


def test_clearing_the_history_forgets_undo_and_redo(tagging_session):
    tagging_session.log_event(EventType.STOPPAGE, 0)
    tagging_session.log_event(EventType.STOPPAGE, 1_000)
    tagging_session.undo()

    tagging_session.clear_history()

    assert not tagging_session.can_undo
    assert not tagging_session.can_redo


def test_undo_and_redo_count_as_activity_on_the_game(tagging_session, session):
    game = session.get(Game, tagging_session.game_id)
    tagging_session.log_event(EventType.STOPPAGE, 0)
    logged_at = game.updated_at

    tagging_session.undo()
    undone_at = game.updated_at
    tagging_session.redo()

    assert logged_at < undone_at < game.updated_at
