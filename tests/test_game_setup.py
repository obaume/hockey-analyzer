from __future__ import annotations

import pytest

from hockey_analyzer.domain.enums import Position
from hockey_analyzer.domain.game_setup import DuplicateJerseyNumberError
from hockey_analyzer.domain.models import Game, GameRosterEntry, Player, Team


# -- create_game: no pre-existing data required ---------------------------


def test_create_game_requires_no_pre_existing_data(game_setup_service, session):
    game = game_setup_service.create_game()

    assert game.id is not None
    assert session.get(Game, game.id) is not None


def test_create_game_commits_immediately(game_setup_service, session):
    game = game_setup_service.create_game()

    # A second, independent look via the same session sees it without an
    # extra commit call from the test itself.
    session.expire_all()
    assert session.get(Game, game.id) is not None


# -- teams: create new or pick existing, symmetric for either side --------


def test_create_team_persists_and_is_listed(game_setup_service):
    team = game_setup_service.create_team("Icebreakers")

    assert team.id is not None
    assert team.name == "Icebreakers"
    assert team in game_setup_service.list_teams()


def test_create_team_defaults_is_user_team_false(game_setup_service):
    team = game_setup_service.create_team("Rivals")
    assert team.is_user_team is False


def test_create_team_can_be_flagged_as_the_users_own(game_setup_service):
    team = game_setup_service.create_team("Icebreakers", is_user_team=True)
    assert team.is_user_team is True


def test_list_teams_includes_teams_created_outside_this_service(game_setup_service, session):
    session.add(Team(name="Pre-existing"))
    session.commit()

    names = {team.name for team in game_setup_service.list_teams()}
    assert "Pre-existing" in names


# -- players: create brand-new on the spot, full_name optional ------------


def test_create_player_with_no_full_name_is_allowed(game_setup_service):
    player = game_setup_service.create_player()

    assert player.id is not None
    assert player.full_name is None


def test_create_player_with_full_name(game_setup_service):
    player = game_setup_service.create_player(full_name="Jordan Kim")
    assert player.full_name == "Jordan Kim"


def test_create_player_with_position(game_setup_service):
    player = game_setup_service.create_player(full_name="Jordan Kim", position=Position.CENTER)
    assert player.position is Position.CENTER


def test_set_player_full_name_backfills_a_skipped_name(game_setup_service, session):
    player = game_setup_service.create_player()
    assert player.full_name is None

    updated = game_setup_service.set_player_full_name(player.id, "Jordan Kim")

    assert updated.full_name == "Jordan Kim"
    assert session.get(Player, player.id).full_name == "Jordan Kim"


def test_set_player_full_name_raises_for_a_missing_player(game_setup_service):
    with pytest.raises(KeyError):
        game_setup_service.set_player_full_name(999999, "Nobody")


def test_set_player_position_is_a_player_level_attribute_not_per_game(game_setup_service, session):
    player = game_setup_service.create_player(full_name="Jordan Kim")

    updated = game_setup_service.set_player_position(player.id, Position.DEFENSE)

    assert updated.position is Position.DEFENSE
    assert session.get(Player, player.id).position is Position.DEFENSE


def test_set_player_position_raises_for_a_missing_player(game_setup_service):
    with pytest.raises(KeyError):
        game_setup_service.set_player_position(999999, Position.GOALIE)


# -- roster entries: add a player to a team's roster for this game --------


def test_add_roster_entry_creates_a_brand_new_player_on_the_spot(game_setup_service, session):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")

    entry = game_setup_service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=14, full_name="Jordan Kim")

    assert entry.id is not None
    assert entry.jersey_number == 14
    assert entry.game_id == game.id
    assert entry.team_id == team.id
    player = session.get(Player, entry.player_id)
    assert player is not None
    assert player.full_name == "Jordan Kim"


def test_add_roster_entry_with_no_full_name_creates_a_nameless_player(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")

    entry = game_setup_service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=14)

    assert entry.player.full_name is None


def test_add_roster_entry_with_position_sets_it_on_the_new_player(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")

    entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=14, full_name="Jordan Kim", position=Position.LEFT_WING
    )

    assert entry.player.position is Position.LEFT_WING


def test_add_roster_entry_can_roster_an_existing_player(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player = game_setup_service.create_player(full_name="Jordan Kim")

    entry = game_setup_service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=14, player_id=player.id)

    assert entry.player_id == player.id


def test_add_roster_entry_rejects_full_name_when_rostering_an_existing_player(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player = game_setup_service.create_player(full_name="Jordan Kim")

    with pytest.raises(ValueError):
        game_setup_service.add_roster_entry(
            game_id=game.id, team_id=team.id, jersey_number=14, player_id=player.id, full_name="Someone Else"
        )


def test_add_roster_entry_rejects_position_when_rostering_an_existing_player(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player = game_setup_service.create_player(full_name="Jordan Kim")

    with pytest.raises(ValueError):
        game_setup_service.add_roster_entry(
            game_id=game.id, team_id=team.id, jersey_number=14, player_id=player.id, position=Position.CENTER
        )


def test_add_roster_entry_works_identically_for_either_side_of_the_game(game_setup_service):
    # No special-cased "opponent" path -- the same method, with the same
    # arguments, rosters a player for either team.
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers", is_user_team=True)
    away = game_setup_service.create_team("Rivals")

    home_entry = game_setup_service.add_roster_entry(game_id=game.id, team_id=home.id, jersey_number=9, full_name="Home Player")
    away_entry = game_setup_service.add_roster_entry(game_id=game.id, team_id=away.id, jersey_number=9, full_name="Away Player")

    assert home_entry.team_id == home.id
    assert away_entry.team_id == away.id


def test_add_roster_entry_rejects_a_duplicate_jersey_number_on_the_same_team(game_setup_service, session):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    game_setup_service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=14, full_name="Jordan Kim")

    with pytest.raises(DuplicateJerseyNumberError):
        game_setup_service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=14, full_name="Casey Nguyen")

    # Rejected outright -- no orphan roster entry or duplicate row leaks in.
    entries = session.query(GameRosterEntry).filter_by(game_id=game.id, team_id=team.id, jersey_number=14).all()
    assert len(entries) == 1


def test_add_roster_entry_allows_the_same_jersey_number_on_different_teams(game_setup_service):
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.add_roster_entry(game_id=game.id, team_id=home.id, jersey_number=14, full_name="Home Player")

    entry = game_setup_service.add_roster_entry(game_id=game.id, team_id=away.id, jersey_number=14, full_name="Away Player")

    assert entry.jersey_number == 14


def test_list_roster_returns_only_this_games_this_teams_entries(game_setup_service):
    game = game_setup_service.create_game()
    other_game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.add_roster_entry(game_id=game.id, team_id=home.id, jersey_number=9, full_name="Home Player")
    game_setup_service.add_roster_entry(game_id=game.id, team_id=away.id, jersey_number=10, full_name="Away Player")
    game_setup_service.add_roster_entry(game_id=other_game.id, team_id=home.id, jersey_number=9, full_name="Other Game Player")

    roster = game_setup_service.list_roster(game.id, home.id)

    assert [entry.jersey_number for entry in roster] == [9]


def test_list_roster_is_ordered_by_jersey_number(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    game_setup_service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=27, full_name="B")
    game_setup_service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=4, full_name="A")

    roster = game_setup_service.list_roster(game.id, team.id)

    assert [entry.jersey_number for entry in roster] == [4, 27]
