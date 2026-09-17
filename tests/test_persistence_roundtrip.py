from __future__ import annotations

import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from hockey_analyzer.domain.enums import Handedness, Position, UnitType
from hockey_analyzer.domain.models import Game, GameRosterEntry, GameUnitAssignment, Player, Team


def test_team_round_trip(session):
    team = Team(name="Icebreakers", league_id="league-42", is_user_team=True)
    session.add(team)
    session.commit()

    fetched = session.get(Team, team.id)
    assert fetched.name == "Icebreakers"
    assert fetched.league_id == "league-42"
    assert fetched.is_user_team is True


def test_team_is_user_team_not_a_singleton(session):
    team_a = Team(name="A", is_user_team=True)
    team_b = Team(name="B", is_user_team=True)
    session.add_all([team_a, team_b])
    session.commit()

    assert session.get(Team, team_a.id).is_user_team is True
    assert session.get(Team, team_b.id).is_user_team is True


def test_player_round_trip(session):
    player = Player(full_name="Alex Rivera", handedness=Handedness.LEFT, position=Position.CENTER)
    session.add(player)
    session.commit()

    fetched = session.get(Player, player.id)
    assert fetched.full_name == "Alex Rivera"
    assert fetched.handedness is Handedness.LEFT
    assert fetched.position is Position.CENTER


def test_player_identity_is_internal_id_not_name_or_jersey(session):
    # Two distinct Player rows may legitimately share a full_name; nothing
    # at the model level treats name as an identity key.
    a = Player(full_name="Sam Lee")
    b = Player(full_name="Sam Lee")
    session.add_all([a, b])
    session.commit()

    assert a.id != b.id


def test_game_round_trip_with_scores_and_period_scores(session):
    game = Game(
        league_id="game-123",
        date=datetime.date(2026, 1, 15),
        venue="Community Rink",
        home_score=4,
        away_score=2,
        period_scores=[{"home": 1, "away": 0}, {"home": 2, "away": 1}, {"home": 1, "away": 1}],
    )
    session.add(game)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched.venue == "Community Rink"
    assert fetched.home_score == 4
    assert fetched.away_score == 2
    assert fetched.period_scores == [
        {"home": 1, "away": 0},
        {"home": 2, "away": 1},
        {"home": 1, "away": 1},
    ]


def test_game_opponent_shifts_complete_defaults_false(session):
    game = Game()
    session.add(game)
    session.commit()

    assert session.get(Game, game.id).opponent_shifts_complete is False


def test_game_roster_entry_round_trip(session, game_and_teams):
    game, team, _ = game_and_teams
    player = Player(full_name="Jordan Kim")
    session.add(player)
    session.flush()

    entry = GameRosterEntry(
        game_id=game.id,
        player_id=player.id,
        team_id=team.id,
        jersey_number=14,
        position=Position.RIGHT_WING,
    )
    session.add(entry)
    session.commit()

    fetched = session.get(GameRosterEntry, entry.id)
    assert fetched.jersey_number == 14
    assert fetched.position is Position.RIGHT_WING
    assert fetched.game_id == game.id
    assert fetched.player_id == player.id
    assert fetched.team_id == team.id


def test_game_roster_entry_jersey_number_unique_within_game_and_team(session, game_and_teams):
    game, team, _ = game_and_teams
    player_a = Player(full_name="Jordan Kim")
    player_b = Player(full_name="Casey Nguyen")
    session.add_all([player_a, player_b])
    session.flush()

    session.add(GameRosterEntry(game_id=game.id, player_id=player_a.id, team_id=team.id, jersey_number=14))
    session.commit()

    session.add(GameRosterEntry(game_id=game.id, player_id=player_b.id, team_id=team.id, jersey_number=14))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_game_roster_entry_same_jersey_number_allowed_across_teams(session, game_and_teams):
    game, team_a, team_b = game_and_teams
    player_a = Player(full_name="Jordan Kim")
    player_b = Player(full_name="Casey Nguyen")
    session.add_all([player_a, player_b])
    session.flush()

    session.add(GameRosterEntry(game_id=game.id, player_id=player_a.id, team_id=team_a.id, jersey_number=14))
    session.add(GameRosterEntry(game_id=game.id, player_id=player_b.id, team_id=team_b.id, jersey_number=14))
    session.commit()  # no error


def test_game_unit_assignment_round_trip(session, game_and_teams):
    game, team, _ = game_and_teams
    player = Player(full_name="Jordan Kim")
    session.add(player)
    session.flush()

    assignment = GameUnitAssignment(
        game_id=game.id,
        player_id=player.id,
        team_id=team.id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=1,
    )
    session.add(assignment)
    session.commit()

    fetched = session.get(GameUnitAssignment, assignment.id)
    assert fetched.unit_type is UnitType.FORWARD_LINE
    assert fetched.unit_number == 1


def test_player_can_hold_several_simultaneous_unit_assignments(session, game_and_teams):
    game, team, _ = game_and_teams
    player = Player(full_name="Jordan Kim")
    session.add(player)
    session.flush()

    session.add(
        GameUnitAssignment(
            game_id=game.id, player_id=player.id, team_id=team.id, unit_type=UnitType.FORWARD_LINE, unit_number=1
        )
    )
    session.add(
        GameUnitAssignment(
            game_id=game.id, player_id=player.id, team_id=team.id, unit_type=UnitType.POWER_PLAY, unit_number=1
        )
    )
    session.commit()  # no error: different unit_type


def test_game_unit_assignment_unique_within_game_player_and_unit_type(session, game_and_teams):
    game, team, _ = game_and_teams
    player = Player(full_name="Jordan Kim")
    session.add(player)
    session.flush()

    session.add(
        GameUnitAssignment(
            game_id=game.id, player_id=player.id, team_id=team.id, unit_type=UnitType.FORWARD_LINE, unit_number=1
        )
    )
    session.commit()

    session.add(
        GameUnitAssignment(
            game_id=game.id, player_id=player.id, team_id=team.id, unit_type=UnitType.FORWARD_LINE, unit_number=2
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
