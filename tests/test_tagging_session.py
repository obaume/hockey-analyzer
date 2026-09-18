from __future__ import annotations

import pytest

from hockey_analyzer.db import create_sqlite_engine, init_db, make_session_factory
from hockey_analyzer.domain.enums import EventType
from hockey_analyzer.domain.models import Event, Game, GameRosterEntry, Player, Stoppage, Team
from hockey_analyzer.domain.tagging_session import TaggingSession

# -- log_event: instant capture -------------------------------------------


def test_log_event_captures_timestamp_and_commits_immediately(tagging_session, session):
    event = tagging_session.log_event(EventType.STOPPAGE, 612)

    assert event.id is not None
    assert event.video_timestamp == 612
    assert session.get(Stoppage, event.id) is not None


def test_log_event_defaults_required_player_reference_to_unknown(tagging_session):
    penalty = tagging_session.log_event(EventType.PENALTY, 400)
    assert penalty.penalty_player_id is None
    assert penalty.penalty_player_unknown is True

    shift = tagging_session.log_event(EventType.SHIFT_CHANGE, 100)
    assert shift.shift_player_id is None
    assert shift.shift_player_unknown is True


def test_log_event_with_no_shift_data_leaves_strength_state_unset(tagging_session):
    event = tagging_session.log_event(EventType.STOPPAGE, 612)
    assert event.strength_state is None


def test_log_event_explicit_strength_state_overrides_the_computed_default(tagging_session):
    event = tagging_session.log_event(EventType.STOPPAGE, 612, strength_state="4v4")
    assert event.strength_state == "4v4"


def test_log_event_does_not_pause_or_touch_playback(tagging_session):
    # TaggingSession has no reference to a player/controller at all --
    # logging an event can never pause playback because there is nothing
    # here capable of doing so.
    assert not hasattr(tagging_session, "player")
    assert not hasattr(tagging_session, "controller")


# -- update_event: edit-anytime -------------------------------------------


def test_update_event_edits_a_field_and_persists(tagging_session, session):
    event = tagging_session.log_event(EventType.PERIOD_START, 0)

    updated = tagging_session.update_event(event.id, period_number=1)

    assert updated.period_number == 1
    assert session.get(type(event), event.id).period_number == 1


def test_update_event_rejects_an_unknown_field_name(tagging_session):
    event = tagging_session.log_event(EventType.STOPPAGE, 0)

    with pytest.raises(ValueError):
        tagging_session.update_event(event.id, not_a_real_field=True)


def test_update_event_raises_for_a_missing_event_id(tagging_session):
    with pytest.raises(KeyError):
        tagging_session.update_event(999999, video_timestamp=1)


def test_update_event_can_revisit_an_already_edited_event(tagging_session):
    # Edit-anytime, not append-only: a field can be corrected more than
    # once, and earlier events remain editable after later ones exist.
    event = tagging_session.log_event(EventType.PERIOD_START, 0, strength_state="5v5")
    tagging_session.log_event(EventType.STOPPAGE, 50)

    tagging_session.update_event(event.id, period_number=1)
    twice_updated = tagging_session.update_event(event.id, period_number=2)

    assert twice_updated.period_number == 2


# -- delete_event -----------------------------------------------------------


def test_delete_event_removes_it(tagging_session, session):
    event = tagging_session.log_event(EventType.STOPPAGE, 612)

    tagging_session.delete_event(event.id)

    assert session.get(Event, event.id) is None


def test_delete_event_raises_for_a_missing_event_id(tagging_session):
    with pytest.raises(KeyError):
        tagging_session.delete_event(999999)


# -- list_events --------------------------------------------------------


def test_list_events_is_ordered_by_video_timestamp_not_insertion_order(tagging_session):
    tagging_session.log_event(EventType.STOPPAGE, 500)
    tagging_session.log_event(EventType.PERIOD_START, 0)
    tagging_session.log_event(EventType.PENALTY, 250)

    ordered = [event.video_timestamp for event in tagging_session.list_events()]

    assert ordered == [0, 250, 500]


def test_list_events_only_returns_this_games_events(session, game_and_teams):
    game, team_a, team_b = game_and_teams
    other_game = Game()
    session.add(other_game)
    session.flush()

    this_session = TaggingSession(session, game_id=game.id, home_team_id=team_a.id, away_team_id=team_b.id)
    other_session = TaggingSession(session, game_id=other_game.id, home_team_id=team_a.id, away_team_id=team_b.id)
    this_session.log_event(EventType.STOPPAGE, 10)
    other_session.log_event(EventType.STOPPAGE, 20)

    assert [event.video_timestamp for event in this_session.list_events()] == [10]


# -- on-the-fly roster creation / player identification --------------------


def test_set_player_reference_creates_a_player_and_roster_entry_on_the_fly(tagging_session, session):
    event = tagging_session.log_event(EventType.SHIFT_CHANGE, 100)

    updated = tagging_session.set_player_reference(event.id, "home", jersey_number=14, full_name="Jordan Kim")

    assert updated.shift_player_unknown is False
    assert updated.shift_player_id is not None
    player = session.get(Player, updated.shift_player_id)
    assert player.full_name == "Jordan Kim"
    entry = session.query(GameRosterEntry).filter_by(
        game_id=tagging_session.game_id, team_id=tagging_session.home_team_id, jersey_number=14
    ).one()
    assert entry.player_id == player.id


def test_set_player_reference_sets_the_teams_column_too(tagging_session):
    event = tagging_session.log_event(EventType.PENALTY, 400)

    updated = tagging_session.set_player_reference(event.id, "away", jersey_number=9)

    assert updated.penalty_team_id == tagging_session.away_team_id


def test_set_player_reference_reuses_an_existing_roster_entry(tagging_session, session):
    first = tagging_session.log_event(EventType.SHIFT_CHANGE, 100)
    second = tagging_session.log_event(EventType.SHIFT_CHANGE, 200)

    tagging_session.set_player_reference(first.id, "home", jersey_number=14)
    tagging_session.set_player_reference(second.id, "home", jersey_number=14)

    assert first.shift_player_id == second.shift_player_id
    assert session.query(Player).count() == 1
    assert session.query(GameRosterEntry).count() == 1


def test_set_player_reference_scopes_roster_lookup_by_team(tagging_session):
    # The same jersey number on both teams must resolve to two different
    # players -- jersey numbers are only unique within (game, team).
    home_event = tagging_session.log_event(EventType.SHIFT_CHANGE, 100)
    away_event = tagging_session.log_event(EventType.SHIFT_CHANGE, 200)

    tagging_session.set_player_reference(home_event.id, "home", jersey_number=14)
    tagging_session.set_player_reference(away_event.id, "away", jersey_number=14)

    assert home_event.shift_player_id != away_event.shift_player_id


def test_set_player_reference_unknown_marks_explicit_unknown(tagging_session):
    event = tagging_session.log_event(EventType.PENALTY, 400)

    updated = tagging_session.set_player_reference(event.id, "home", unknown=True)

    assert updated.penalty_player_id is None
    assert updated.penalty_player_unknown is True


def test_set_player_reference_can_switch_from_known_back_to_unknown(tagging_session):
    # Edit-anytime applies to player references too -- a mistaken jersey
    # entry can be walked back to "unknown" rather than left wrong.
    event = tagging_session.log_event(EventType.SHIFT_CHANGE, 100)
    tagging_session.set_player_reference(event.id, "home", jersey_number=14)

    corrected = tagging_session.set_player_reference(event.id, "home", unknown=True)

    assert corrected.shift_player_id is None
    assert corrected.shift_player_unknown is True


def test_set_player_reference_requires_jersey_number_unless_unknown(tagging_session):
    event = tagging_session.log_event(EventType.PENALTY, 400)

    with pytest.raises(ValueError):
        tagging_session.set_player_reference(event.id, "home")


def test_set_player_reference_rejects_an_invalid_team_side(tagging_session):
    event = tagging_session.log_event(EventType.PENALTY, 400)

    with pytest.raises(ValueError):
        tagging_session.set_player_reference(event.id, "visitors", jersey_number=9)


def test_set_player_reference_rejects_event_types_with_no_player_reference(tagging_session):
    event = tagging_session.log_event(EventType.STOPPAGE, 400)

    with pytest.raises(ValueError):
        tagging_session.set_player_reference(event.id, "home", jersey_number=9)


def test_resolve_or_create_roster_entry_is_directly_usable(tagging_session, session):
    entry = tagging_session.resolve_or_create_roster_entry(tagging_session.home_team_id, 88)

    assert entry.jersey_number == 88
    assert session.get(Player, entry.player_id) is not None


# -- strength-state defaulting ----------------------------------------------


def test_infer_strength_state_reflects_on_ice_counts_so_far(tagging_session):
    home_players = [(f"H{i}", i) for i in range(5)]
    away_players = [(f"A{i}", i) for i in range(4)]
    for name, jersey in home_players:
        entry = tagging_session.resolve_or_create_roster_entry(tagging_session.home_team_id, jersey, full_name=name)
        on = tagging_session.log_event(EventType.SHIFT_CHANGE, 10)
        tagging_session.set_player_reference(on.id, "home", jersey_number=jersey)
        tagging_session.update_event(on.id, shift_on_ice=True)
    for name, jersey in away_players:
        entry = tagging_session.resolve_or_create_roster_entry(tagging_session.away_team_id, jersey, full_name=name)
        on = tagging_session.log_event(EventType.SHIFT_CHANGE, 10)
        tagging_session.set_player_reference(on.id, "away", jersey_number=jersey)
        tagging_session.update_event(on.id, shift_on_ice=True)

    stoppage = tagging_session.log_event(EventType.STOPPAGE, 20)

    assert stoppage.strength_state == "5v4"


def test_infer_strength_state_reflects_a_player_coming_back_off(tagging_session):
    on = tagging_session.log_event(EventType.SHIFT_CHANGE, 10)
    tagging_session.set_player_reference(on.id, "home", jersey_number=14)
    tagging_session.update_event(on.id, shift_on_ice=True)

    off = tagging_session.log_event(EventType.SHIFT_CHANGE, 15)
    tagging_session.set_player_reference(off.id, "home", jersey_number=14)
    tagging_session.update_event(off.id, shift_on_ice=False)

    stoppage = tagging_session.log_event(EventType.STOPPAGE, 20)

    # Both counts are back to zero, which reads as "nothing to infer from
    # yet" rather than a literal 0v0 strength state.
    assert stoppage.strength_state is None


def test_infer_strength_state_ignores_shift_changes_after_the_cutoff(tagging_session):
    on = tagging_session.log_event(EventType.SHIFT_CHANGE, 10)
    tagging_session.set_player_reference(on.id, "home", jersey_number=14)
    tagging_session.update_event(on.id, shift_on_ice=True)

    # An earlier stoppage shouldn't see a shift change tagged (in video
    # time) after it, even though it was entered into the DB later --
    # edit-anytime tagging can add events out of chronological order.
    earlier_stoppage = tagging_session.log_event(EventType.STOPPAGE, 5)

    assert earlier_stoppage.strength_state is None


def test_infer_strength_state_ignores_unknown_player_shift_changes(tagging_session):
    # Unknown references can't be tracked individually (no id to key on),
    # so they're excluded from the computed guess -- a documented
    # imprecision, not a bug, since the default is always overridable.
    on = tagging_session.log_event(EventType.SHIFT_CHANGE, 10)
    tagging_session.set_player_reference(on.id, "home", unknown=True)
    tagging_session.update_event(on.id, shift_on_ice=True)

    stoppage = tagging_session.log_event(EventType.STOPPAGE, 20)

    assert stoppage.strength_state is None


# -- describe_event / get_event (display helpers for the UI layer) --------


def test_get_event_raises_for_a_missing_id(tagging_session):
    with pytest.raises(KeyError):
        tagging_session.get_event(999999)


def test_describe_event_reports_missing_fields_without_raising(tagging_session):
    penalty = tagging_session.log_event(EventType.PENALTY, 400)
    assert "not set" in tagging_session.describe_event(penalty)


def test_describe_event_includes_jersey_and_team_once_resolved(tagging_session):
    penalty = tagging_session.log_event(EventType.PENALTY, 400)
    tagging_session.set_player_reference(penalty.id, "home", jersey_number=14, full_name="Jordan Kim")
    tagging_session.update_event(penalty.id, penalty_infraction="tripping", penalty_duration_minutes=2.0)

    description = tagging_session.describe_event(penalty)

    assert "#14" in description
    assert "Jordan Kim" in description
    assert "tripping" in description


def test_describe_event_reports_unknown_player(tagging_session):
    shift = tagging_session.log_event(EventType.SHIFT_CHANGE, 100)
    tagging_session.set_player_reference(shift.id, "away", unknown=True)

    assert "unknown" in tagging_session.describe_event(shift)


# -- resumability / persistence across sessions -----------------------------


def test_session_survives_being_closed_and_reopened(tmp_path):
    db_path = tmp_path / "game.db"
    engine = create_sqlite_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)

    with factory() as db_session:
        team_a = Team(name="Icebreakers")
        team_b = Team(name="Rivals")
        db_session.add_all([team_a, team_b])
        db_session.flush()
        game = Game()
        db_session.add(game)
        db_session.flush()
        game_id, home_id, away_id = game.id, team_a.id, team_b.id

        tagging_session = TaggingSession(db_session, game_id=game_id, home_team_id=home_id, away_team_id=away_id)
        event = tagging_session.log_event(EventType.PERIOD_START, 0)
        tagging_session.update_event(event.id, period_number=1)
    # Session (and its connection) is now closed -- simulating the app
    # being closed and reopened, per ticket 15's resumability requirement.

    reopened_engine = create_sqlite_engine(db_path)
    reopened_factory = make_session_factory(reopened_engine)
    with reopened_factory() as reopened_db_session:
        resumed = TaggingSession(reopened_db_session, game_id=game_id, home_team_id=home_id, away_team_id=away_id)
        events = resumed.list_events()

        assert len(events) == 1
        assert events[0].period_number == 1
