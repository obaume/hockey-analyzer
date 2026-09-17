from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from hockey_analyzer.domain.enums import EventType, ShotOutcome, ShotType
from hockey_analyzer.domain.models import Faceoff, Penalty, Player, PeriodEnd, PeriodStart, ShiftChange, ShotAttempt, Stoppage
from hockey_analyzer.domain.rink import high_danger, zone


def test_period_start_round_trip(session, game_and_teams):
    game, _, _ = game_and_teams
    event = PeriodStart(
        game_id=game.id,
        video_timestamp=0,
        strength_state="5v5",
        period_number=1,
    )
    session.add(event)
    session.commit()

    fetched = session.get(PeriodStart, event.id)
    assert fetched.event_type is EventType.PERIOD_START
    assert fetched.period_number == 1
    assert fetched.confirmed is False  # default


def test_period_end_round_trip(session, game_and_teams):
    game, _, _ = game_and_teams
    event = PeriodEnd(
        game_id=game.id,
        video_timestamp=1230,
        strength_state="5v5",
        period_number=1,
    )
    session.add(event)
    session.commit()

    assert session.get(PeriodEnd, event.id).period_number == 1


def test_stoppage_round_trip(session, game_and_teams):
    game, _, _ = game_and_teams
    event = Stoppage(
        game_id=game.id,
        video_timestamp=612,
        strength_state="5v5",
    )
    session.add(event)
    session.commit()

    assert session.get(Stoppage, event.id).event_type is EventType.STOPPAGE


def test_faceoff_round_trip_with_known_participants(session, game_and_teams):
    game, team_a, team_b = game_and_teams
    player_a = Player(full_name="Jordan Kim")
    player_b = Player(full_name="Casey Nguyen")
    session.add_all([player_a, player_b])
    session.flush()

    event = Faceoff(
        game_id=game.id,
        video_timestamp=5,
        strength_state="5v5",
        faceoff_x=0.0,
        faceoff_y=0.0,
        faceoff_team_a_id=team_a.id,
        faceoff_participant_a_id=player_a.id,
        faceoff_team_b_id=team_b.id,
        faceoff_participant_b_id=player_b.id,
        faceoff_winner_team_id=team_a.id,
    )
    session.add(event)
    session.commit()

    fetched = session.get(Faceoff, event.id)
    assert fetched.faceoff_x == 0.0
    assert fetched.faceoff_participant_a_id == player_a.id
    assert fetched.faceoff_participant_a_unknown is False
    assert fetched.faceoff_winner_team_id == team_a.id


def test_faceoff_participant_can_be_explicitly_unknown(session, game_and_teams):
    game, team_a, team_b = game_and_teams
    player_a = Player(full_name="Jordan Kim")
    session.add(player_a)
    session.flush()

    event = Faceoff(
        game_id=game.id,
        video_timestamp=5,
        strength_state="5v5",
        faceoff_x=-10.0,
        faceoff_y=3.0,
        faceoff_team_a_id=team_a.id,
        faceoff_participant_a_id=player_a.id,
        faceoff_team_b_id=team_b.id,
        faceoff_participant_b_unknown=True,
    )
    session.add(event)
    session.commit()

    fetched = session.get(Faceoff, event.id)
    assert fetched.faceoff_participant_b_id is None
    assert fetched.faceoff_participant_b_unknown is True


def test_faceoff_zone_and_high_danger_are_derived_not_persisted(session, game_and_teams):
    game, team_a, team_b = game_and_teams
    event = Faceoff(
        game_id=game.id,
        video_timestamp=5,
        strength_state="5v5",
        faceoff_x=40.0,
        faceoff_y=0.0,
        faceoff_team_a_id=team_a.id,
        faceoff_participant_a_unknown=True,
        faceoff_team_b_id=team_b.id,
        faceoff_participant_b_unknown=True,
    )
    session.add(event)
    session.commit()

    assert not hasattr(Faceoff, "zone")
    assert not hasattr(Faceoff, "high_danger")

    fetched = session.get(Faceoff, event.id)
    assert zone(fetched.faceoff_x, attacking_direction=1) == "offensive"


def test_shot_attempt_round_trip_full_fields(session, game_and_teams):
    game, team_a, _ = game_and_teams
    shooter = Player(full_name="Jordan Kim")
    assist1 = Player(full_name="Casey Nguyen")
    session.add_all([shooter, assist1])
    session.flush()

    event = ShotAttempt(
        game_id=game.id,
        video_timestamp=812,
        strength_state="5v5",
        shot_x=85.0,
        shot_y=0.0,
        shot_team_id=team_a.id,
        shot_outcome=ShotOutcome.GOAL,
        shot_type=ShotType.WRIST,
        shot_rush=True,
        shot_rebound=False,
        shot_screened=True,
        shot_one_timer=False,
        shot_xg=0.18,
        shooter_id=shooter.id,
        assist1_id=assist1.id,
        assist2_unknown=False,
    )
    session.add(event)
    session.commit()

    fetched = session.get(ShotAttempt, event.id)
    assert fetched.shot_outcome is ShotOutcome.GOAL
    assert fetched.shot_type is ShotType.WRIST
    assert fetched.shot_rush is True
    assert fetched.shot_screened is True
    assert fetched.shot_one_timer is False
    assert fetched.shot_xg == pytest.approx(0.18)
    assert fetched.shooter_id == shooter.id
    assert fetched.assist1_id == assist1.id
    assert fetched.assist2_id is None
    assert fetched.assist2_unknown is False
    assert high_danger(fetched.shot_x, fetched.shot_y) is True


def test_shot_attempt_missed_has_null_xg_and_no_assists(session, game_and_teams):
    game, team_a, _ = game_and_teams
    shooter = Player(full_name="Jordan Kim")
    session.add(shooter)
    session.flush()

    event = ShotAttempt(
        game_id=game.id,
        video_timestamp=900,
        strength_state="5v5",
        shot_x=30.0,
        shot_y=10.0,
        shot_team_id=team_a.id,
        shot_outcome=ShotOutcome.MISSED,
        shot_type=ShotType.SLAP,
        shooter_id=shooter.id,
    )
    session.add(event)
    session.commit()

    fetched = session.get(ShotAttempt, event.id)
    assert fetched.shot_xg is None
    assert fetched.assist1_id is None
    assert fetched.assist1_unknown is False


def test_shot_attempt_shooter_can_be_explicitly_unknown(session, game_and_teams):
    game, team_a, _ = game_and_teams
    event = ShotAttempt(
        game_id=game.id,
        video_timestamp=900,
        strength_state="5v5",
        shot_x=30.0,
        shot_y=10.0,
        shot_team_id=team_a.id,
        shot_outcome=ShotOutcome.BLOCKED,
        shot_type=ShotType.UNKNOWN,
        shooter_unknown=True,
    )
    session.add(event)
    session.commit()

    fetched = session.get(ShotAttempt, event.id)
    assert fetched.shooter_id is None
    assert fetched.shooter_unknown is True


def test_shot_attempt_requires_shot_type(session, game_and_teams):
    game, team_a, _ = game_and_teams
    event = ShotAttempt(
        game_id=game.id,
        video_timestamp=900,
        strength_state="5v5",
        shot_x=30.0,
        shot_y=10.0,
        shot_team_id=team_a.id,
        shot_outcome=ShotOutcome.MISSED,
        shot_type=None,
        shooter_unknown=True,
    )
    session.add(event)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_shot_attempt_requires_shot_outcome(session, game_and_teams):
    game, team_a, _ = game_and_teams
    event = ShotAttempt(
        game_id=game.id,
        video_timestamp=900,
        strength_state="5v5",
        shot_x=30.0,
        shot_y=10.0,
        shot_team_id=team_a.id,
        shot_outcome=None,
        shot_type=ShotType.WRIST,
        shooter_unknown=True,
    )
    session.add(event)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_shot_attempt_xg_must_be_in_zero_one_range(session, game_and_teams):
    game, team_a, _ = game_and_teams
    event = ShotAttempt(
        game_id=game.id,
        video_timestamp=900,
        strength_state="5v5",
        shot_x=30.0,
        shot_y=10.0,
        shot_team_id=team_a.id,
        shot_outcome=ShotOutcome.SAVED,
        shot_type=ShotType.WRIST,
        shooter_unknown=True,
        shot_xg=1.5,
    )
    session.add(event)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_shot_attempt_xg_only_populated_for_shots_on_goal(session, game_and_teams):
    game, team_a, _ = game_and_teams
    event = ShotAttempt(
        game_id=game.id,
        video_timestamp=900,
        strength_state="5v5",
        shot_x=30.0,
        shot_y=10.0,
        shot_team_id=team_a.id,
        shot_outcome=ShotOutcome.MISSED,
        shot_type=ShotType.WRIST,
        shooter_unknown=True,
        shot_xg=0.1,
    )
    session.add(event)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_penalty_round_trip(session, game_and_teams):
    game, team_a, _ = game_and_teams
    player = Player(full_name="Jordan Kim")
    session.add(player)
    session.flush()

    event = Penalty(
        game_id=game.id,
        video_timestamp=400,
        strength_state="5v5",
        penalty_team_id=team_a.id,
        penalty_player_id=player.id,
        penalty_duration_minutes=2.0,
        penalty_infraction="tripping",
    )
    session.add(event)
    session.commit()

    fetched = session.get(Penalty, event.id)
    assert fetched.penalty_duration_minutes == 2.0
    assert fetched.penalty_infraction == "tripping"
    assert fetched.penalty_player_id == player.id


def test_penalty_player_can_be_explicitly_unknown(session, game_and_teams):
    game, team_a, _ = game_and_teams
    event = Penalty(
        game_id=game.id,
        video_timestamp=400,
        strength_state="5v5",
        penalty_team_id=team_a.id,
        penalty_player_unknown=True,
        penalty_duration_minutes=2.0,
        penalty_infraction="interference",
    )
    session.add(event)
    session.commit()

    fetched = session.get(Penalty, event.id)
    assert fetched.penalty_player_id is None
    assert fetched.penalty_player_unknown is True


def test_shift_change_round_trip(session, game_and_teams):
    game, team_a, _ = game_and_teams
    player = Player(full_name="Jordan Kim")
    session.add(player)
    session.flush()

    event = ShiftChange(
        game_id=game.id,
        video_timestamp=100,
        strength_state="5v5",
        shift_team_id=team_a.id,
        shift_player_id=player.id,
        shift_on_ice=True,
    )
    session.add(event)
    session.commit()

    fetched = session.get(ShiftChange, event.id)
    assert fetched.shift_on_ice is True
    assert fetched.shift_player_id == player.id


def test_shift_change_player_can_be_explicitly_unknown(session, game_and_teams):
    game, team_a, _ = game_and_teams
    event = ShiftChange(
        game_id=game.id,
        video_timestamp=100,
        strength_state="5v5",
        shift_team_id=team_a.id,
        shift_player_unknown=True,
        shift_on_ice=False,
    )
    session.add(event)
    session.commit()

    fetched = session.get(ShiftChange, event.id)
    assert fetched.shift_player_id is None
    assert fetched.shift_player_unknown is True


def test_required_reference_rejects_both_id_and_unknown_set(session, game_and_teams):
    game, team_a, _ = game_and_teams
    player = Player(full_name="Jordan Kim")
    session.add(player)
    session.flush()

    event = ShiftChange(
        game_id=game.id,
        video_timestamp=100,
        strength_state="5v5",
        shift_team_id=team_a.id,
        shift_player_id=player.id,
        shift_player_unknown=True,  # invalid: both a known id and unknown=True
        shift_on_ice=True,
    )
    session.add(event)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_required_reference_rejects_neither_id_nor_unknown_set(session, game_and_teams):
    game, team_a, _ = game_and_teams

    event = ShiftChange(
        game_id=game.id,
        video_timestamp=100,
        strength_state="5v5",
        shift_team_id=team_a.id,
        # neither shift_player_id nor shift_player_unknown set
        shift_on_ice=True,
    )
    session.add(event)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_optional_reference_rejects_both_id_and_unknown_set(session, game_and_teams):
    game, team_a, _ = game_and_teams
    shooter = Player(full_name="Jordan Kim")
    assist = Player(full_name="Casey Nguyen")
    session.add_all([shooter, assist])
    session.flush()

    event = ShotAttempt(
        game_id=game.id,
        video_timestamp=900,
        strength_state="5v5",
        shot_x=30.0,
        shot_y=10.0,
        shot_team_id=team_a.id,
        shot_outcome=ShotOutcome.GOAL,
        shot_type=ShotType.WRIST,
        shooter_id=shooter.id,
        assist1_id=assist.id,
        assist1_unknown=True,  # invalid: an assist can't be both known and unknown
    )
    session.add(event)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
