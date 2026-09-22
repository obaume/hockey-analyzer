from __future__ import annotations

import pytest

from hockey_analyzer.db import create_sqlite_engine, init_db, make_session_factory
from hockey_analyzer.domain.enums import EventType, RinkType, ShotOutcome, ShotType
from hockey_analyzer.domain.models import Event, Game, GameRosterEntry, Player, Stoppage, Team
from hockey_analyzer.domain.rink import high_danger, zone
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


# -- rink_type: the tagged game's rink standard, exposed read-only ---------


def test_rink_type_reflects_the_games_rink_type(session, game_and_teams):
    game, team_a, team_b = game_and_teams
    game.rink_type = RinkType.NHL
    session.commit()

    tagging_session = TaggingSession(session, game_id=game.id, home_team_id=team_a.id, away_team_id=team_b.id)

    assert tagging_session.rink_type is RinkType.NHL


def test_rink_type_defaults_to_iihf_for_a_game_created_via_game_setup_service(session, game_and_teams):
    game, team_a, team_b = game_and_teams
    tagging_session = TaggingSession(session, game_id=game.id, home_team_id=team_a.id, away_team_id=team_b.id)

    assert tagging_session.rink_type is RinkType.IIHF


def test_tagging_session_has_no_way_to_change_rink_type(tagging_session):
    # Read-only by design -- immutable once the game is created (see
    # ADR-0008/CONTEXT.md's Rink type entry).
    assert not hasattr(tagging_session, "set_rink_type")


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


# -- location-bearing events: faceoff / shot_attempt (ticket 16) ----------


def test_log_event_faceoff_defaults_both_participants_to_unknown(tagging_session):
    faceoff = tagging_session.log_event(EventType.FACEOFF, 5)

    assert faceoff.faceoff_participant_a_id is None
    assert faceoff.faceoff_participant_a_unknown is True
    assert faceoff.faceoff_participant_b_id is None
    assert faceoff.faceoff_participant_b_unknown is True


def test_log_event_shot_attempt_defaults_shooter_to_unknown_but_not_assists(tagging_session):
    shot = tagging_session.log_event(
        EventType.SHOT_ATTEMPT, 812, shot_outcome=ShotOutcome.SAVED, shot_type=ShotType.WRIST
    )

    assert shot.shooter_id is None
    assert shot.shooter_unknown is True
    # Assists are optional -- "no assist" is a real, unflagged absence, not
    # a stub state that needs defaulting to explicit unknown.
    assert shot.assist1_id is None
    assert shot.assist1_unknown is False
    assert shot.assist2_id is None
    assert shot.assist2_unknown is False


def test_log_event_shot_attempt_requires_outcome_and_shot_type(tagging_session):
    # Unlike every other in-scope event type, shot_attempt's outcome/type
    # are always-required columns at the DB level (models.py CHECK
    # constraints, no "unknown" stand-in for outcome) -- so, unlike the
    # rest of log_event's "instant, blank stub" contract, these two can't
    # be deferred to a later update_event call.
    with pytest.raises(ValueError):
        tagging_session.log_event(EventType.SHOT_ATTEMPT, 812, shot_type=ShotType.WRIST)

    with pytest.raises(ValueError):
        tagging_session.log_event(EventType.SHOT_ATTEMPT, 812, shot_outcome=ShotOutcome.SAVED)


def test_update_event_stores_faceoff_location_and_zone_is_derivable_at_read_time(tagging_session):
    faceoff = tagging_session.log_event(EventType.FACEOFF, 5)

    updated = tagging_session.update_event(faceoff.id, faceoff_x=40.0, faceoff_y=0.0)

    assert updated.faceoff_x == 40.0
    assert updated.faceoff_y == 0.0
    assert zone(updated.faceoff_x, attacking_direction=1, rink_type=RinkType.IIHF) == "offensive"


def test_update_event_stores_shot_location_and_high_danger_is_derivable_at_read_time(tagging_session):
    shot = tagging_session.log_event(
        EventType.SHOT_ATTEMPT, 812, shot_outcome=ShotOutcome.SAVED, shot_type=ShotType.WRIST
    )

    updated = tagging_session.update_event(shot.id, shot_x=85.0, shot_y=0.0)

    assert updated.shot_x == 85.0
    assert high_danger(updated.shot_x, updated.shot_y, rink_type=RinkType.IIHF) is True


def test_update_event_can_revise_shot_type_outcome_and_set_shot_context_flags(tagging_session):
    # Edit-anytime still applies past the initial required capture -- a
    # tagger who got the outcome wrong in the heat of the moment can
    # correct it afterward, same as every other field.
    shot = tagging_session.log_event(
        EventType.SHOT_ATTEMPT, 812, shot_outcome=ShotOutcome.SAVED, shot_type=ShotType.SLAP
    )

    updated = tagging_session.update_event(
        shot.id,
        shot_type=ShotType.WRIST,
        shot_outcome=ShotOutcome.GOAL,
        shot_rush=True,
        shot_rebound=False,
        shot_screened=True,
        shot_one_timer=False,
    )

    assert updated.shot_type is ShotType.WRIST
    assert updated.shot_outcome is ShotOutcome.GOAL
    assert updated.shot_rush is True
    assert updated.shot_screened is True
    assert updated.shot_rebound is False
    assert updated.shot_one_timer is False


def test_log_event_shot_attempt_leaves_xg_null_with_no_calibration_logic(tagging_session):
    shot = tagging_session.log_event(
        EventType.SHOT_ATTEMPT, 812, shot_outcome=ShotOutcome.GOAL, shot_type=ShotType.WRIST
    )
    assert shot.shot_xg is None


def test_set_player_reference_requires_an_explicit_reference_for_faceoff(tagging_session):
    faceoff = tagging_session.log_event(EventType.FACEOFF, 5)

    with pytest.raises(ValueError):
        tagging_session.set_player_reference(faceoff.id, "home", jersey_number=14)


def test_set_player_reference_sets_faceoff_participant_a(tagging_session, session):
    faceoff = tagging_session.log_event(EventType.FACEOFF, 5)

    updated = tagging_session.set_player_reference(
        faceoff.id, "home", reference="participant_a", jersey_number=14, full_name="Jordan Kim"
    )

    assert updated.faceoff_participant_a_unknown is False
    assert updated.faceoff_team_a_id == tagging_session.home_team_id
    player = session.get(Player, updated.faceoff_participant_a_id)
    assert player.full_name == "Jordan Kim"


def test_set_player_reference_sets_faceoff_participant_b_independently(tagging_session):
    faceoff = tagging_session.log_event(EventType.FACEOFF, 5)
    tagging_session.set_player_reference(faceoff.id, "home", reference="participant_a", jersey_number=14)

    updated = tagging_session.set_player_reference(faceoff.id, "away", reference="participant_b", jersey_number=9)

    assert updated.faceoff_team_b_id == tagging_session.away_team_id
    assert updated.faceoff_participant_b_unknown is False
    # Setting b must not disturb a's already-resolved reference.
    assert updated.faceoff_participant_a_unknown is False
    assert updated.faceoff_team_a_id == tagging_session.home_team_id


def _log_shot(tagging_session, video_timestamp=812, **overrides):
    fields = {"shot_outcome": ShotOutcome.SAVED, "shot_type": ShotType.WRIST, **overrides}
    return tagging_session.log_event(EventType.SHOT_ATTEMPT, video_timestamp, **fields)


def test_set_player_reference_requires_an_explicit_reference_for_shot_attempt(tagging_session):
    shot = _log_shot(tagging_session)

    with pytest.raises(ValueError):
        tagging_session.set_player_reference(shot.id, "home", jersey_number=9)


def test_set_player_reference_sets_the_shooter(tagging_session):
    shot = _log_shot(tagging_session)

    updated = tagging_session.set_player_reference(shot.id, "home", reference="shooter", jersey_number=9)

    assert updated.shooter_unknown is False
    assert updated.shot_team_id == tagging_session.home_team_id


def test_set_player_reference_sets_an_assist_without_touching_shot_team(tagging_session):
    shot = _log_shot(tagging_session)
    tagging_session.set_player_reference(shot.id, "home", reference="shooter", jersey_number=9)

    updated = tagging_session.set_player_reference(shot.id, "home", reference="assist1", jersey_number=14)

    assert updated.assist1_unknown is False
    assert updated.assist1_id is not None
    # Assists have no dedicated team column (see models.py) -- only the
    # shooter's resolution writes shot_team_id.
    assert updated.shot_team_id == tagging_session.home_team_id


def test_set_player_reference_assist_can_be_marked_unknown(tagging_session):
    shot = _log_shot(tagging_session)

    updated = tagging_session.set_player_reference(shot.id, "home", reference="assist2", unknown=True)

    assert updated.assist2_id is None
    assert updated.assist2_unknown is True


def test_set_player_reference_rejects_an_unknown_reference_name(tagging_session):
    shot = _log_shot(tagging_session)

    with pytest.raises(ValueError):
        tagging_session.set_player_reference(shot.id, "home", reference="goalie", jersey_number=1)


def test_describe_event_faceoff_reports_both_participants(tagging_session):
    faceoff = tagging_session.log_event(EventType.FACEOFF, 5)
    tagging_session.set_player_reference(
        faceoff.id, "home", reference="participant_a", jersey_number=14, full_name="Jordan Kim"
    )
    tagging_session.set_player_reference(faceoff.id, "away", reference="participant_b", unknown=True)

    description = tagging_session.describe_event(faceoff)

    assert "#14" in description
    assert "Jordan Kim" in description
    assert "unknown" in description
    assert "vs" in description


def test_describe_event_shot_attempt_reports_shooter_and_outcome(tagging_session):
    shot = _log_shot(tagging_session, shot_outcome=ShotOutcome.GOAL, shot_type=ShotType.WRIST)
    tagging_session.set_player_reference(shot.id, "home", reference="shooter", jersey_number=9)

    description = tagging_session.describe_event(shot)

    assert "#9" in description
    assert "goal" in description


def test_describe_event_shot_attempt_reports_unknown_shooter_without_raising(tagging_session):
    shot = _log_shot(tagging_session, shot_outcome=ShotOutcome.MISSED, shot_type=ShotType.UNKNOWN)

    description = tagging_session.describe_event(shot)

    assert "unknown" in description
    assert "missed" in description


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
