from __future__ import annotations

import pytest

from hockey_analyzer.domain.enums import (
    EventSource,
    Position,
    RinkType,
    ShotOutcome,
    ShotType,
    UnitType,
)
from hockey_analyzer.domain.game_setup import (
    DuplicateJerseyNumberError,
    GameSetupService,
    PlayerNotRosteredError,
    RosterEntryInUseError,
    SameTeamBothSidesError,
    SharedEventsError,
)
from hockey_analyzer.domain.models import (
    Faceoff,
    Game,
    GameRosterEntry,
    GameUnitAssignment,
    Penalty,
    Player,
    ShiftChange,
    ShotAttempt,
    Stoppage,
    Team,
)

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


# -- create_game: rink_type -------------------------------------------------


def test_create_game_defaults_rink_type_to_iihf(game_setup_service):
    game = game_setup_service.create_game()
    assert game.rink_type is RinkType.IIHF


def test_create_game_accepts_an_explicit_rink_type(game_setup_service):
    game = game_setup_service.create_game(rink_type=RinkType.NHL)
    assert game.rink_type is RinkType.NHL


def test_game_setup_service_has_no_way_to_change_rink_type_after_creation(
    game_setup_service,
):
    # Immutable by design (see ADR-0008/CONTEXT.md's Rink type entry) --
    # there is deliberately no set_rink_type or equivalent update path.
    assert not hasattr(game_setup_service, "set_rink_type")
    assert not hasattr(game_setup_service, "update_rink_type")


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


def test_list_teams_includes_teams_created_outside_this_service(
    game_setup_service, session
):
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
    player = game_setup_service.create_player(
        full_name="Jordan Kim", position=Position.CENTER
    )
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


def test_set_player_position_is_a_player_level_attribute_not_per_game(
    game_setup_service, session
):
    player = game_setup_service.create_player(full_name="Jordan Kim")

    updated = game_setup_service.set_player_position(player.id, Position.DEFENSE)

    assert updated.position is Position.DEFENSE
    assert session.get(Player, player.id).position is Position.DEFENSE


def test_a_position_given_as_its_plain_value_is_stored_as_the_enum(
    game_setup_service,
):
    # What a QComboBox hands back for a Position item: the bare "D".
    # Kept as-is it would outlive the commit (expire_on_commit=False) and
    # break anything reading `.value` off it.
    player = game_setup_service.create_player(position="G")
    assert player.position is Position.GOALIE

    game_setup_service.set_player_position(player.id, "D")
    assert player.position is Position.DEFENSE


def test_set_player_position_raises_for_a_missing_player(game_setup_service):
    with pytest.raises(KeyError):
        game_setup_service.set_player_position(999999, Position.GOALIE)


# -- roster entries: add a player to a team's roster for this game --------


def test_add_roster_entry_creates_a_brand_new_player_on_the_spot(
    game_setup_service, session
):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")

    entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=14, full_name="Jordan Kim"
    )

    assert entry.id is not None
    assert entry.jersey_number == 14
    assert entry.game_id == game.id
    assert entry.team_id == team.id
    player = session.get(Player, entry.player_id)
    assert player is not None
    assert player.full_name == "Jordan Kim"


def test_add_roster_entry_with_no_full_name_creates_a_nameless_player(
    game_setup_service,
):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")

    entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=14
    )

    assert entry.player.full_name is None


def test_add_roster_entry_with_position_sets_it_on_the_new_player(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")

    entry = game_setup_service.add_roster_entry(
        game_id=game.id,
        team_id=team.id,
        jersey_number=14,
        full_name="Jordan Kim",
        position=Position.LEFT_WING,
    )

    assert entry.player.position is Position.LEFT_WING


def test_add_roster_entry_can_roster_an_existing_player(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player = game_setup_service.create_player(full_name="Jordan Kim")

    entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=14, player_id=player.id
    )

    assert entry.player_id == player.id


def test_add_roster_entry_rejects_full_name_when_rostering_an_existing_player(
    game_setup_service,
):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player = game_setup_service.create_player(full_name="Jordan Kim")

    with pytest.raises(ValueError):
        game_setup_service.add_roster_entry(
            game_id=game.id,
            team_id=team.id,
            jersey_number=14,
            player_id=player.id,
            full_name="Someone Else",
        )


def test_add_roster_entry_rejects_position_when_rostering_an_existing_player(
    game_setup_service,
):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player = game_setup_service.create_player(full_name="Jordan Kim")

    with pytest.raises(ValueError):
        game_setup_service.add_roster_entry(
            game_id=game.id,
            team_id=team.id,
            jersey_number=14,
            player_id=player.id,
            position=Position.CENTER,
        )


def test_add_roster_entry_works_identically_for_either_side_of_the_game(
    game_setup_service,
):
    # No special-cased "opponent" path -- the same method, with the same
    # arguments, rosters a player for either team.
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers", is_user_team=True)
    away = game_setup_service.create_team("Rivals")

    home_entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=9, full_name="Home Player"
    )
    away_entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=away.id, jersey_number=9, full_name="Away Player"
    )

    assert home_entry.team_id == home.id
    assert away_entry.team_id == away.id


def test_add_roster_entry_rejects_a_duplicate_jersey_number_on_the_same_team(
    game_setup_service, session
):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=14, full_name="Jordan Kim"
    )

    with pytest.raises(DuplicateJerseyNumberError):
        game_setup_service.add_roster_entry(
            game_id=game.id, team_id=team.id, jersey_number=14, full_name="Casey Nguyen"
        )

    # Rejected outright -- no orphan roster entry or duplicate row leaks in.
    entries = (
        session.query(GameRosterEntry)
        .filter_by(game_id=game.id, team_id=team.id, jersey_number=14)
        .all()
    )
    assert len(entries) == 1


def test_add_roster_entry_allows_the_same_jersey_number_on_different_teams(
    game_setup_service,
):
    game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=14, full_name="Home Player"
    )

    entry = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=away.id, jersey_number=14, full_name="Away Player"
    )

    assert entry.jersey_number == 14


def test_list_roster_returns_only_this_games_this_teams_entries(game_setup_service):
    game = game_setup_service.create_game()
    other_game = game_setup_service.create_game()
    home = game_setup_service.create_team("Icebreakers")
    away = game_setup_service.create_team("Rivals")
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=home.id, jersey_number=9, full_name="Home Player"
    )
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=away.id, jersey_number=10, full_name="Away Player"
    )
    game_setup_service.add_roster_entry(
        game_id=other_game.id,
        team_id=home.id,
        jersey_number=9,
        full_name="Other Game Player",
    )

    roster = game_setup_service.list_roster(game.id, home.id)

    assert [entry.jersey_number for entry in roster] == [9]


def test_list_roster_is_ordered_by_jersey_number(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=27, full_name="B"
    )
    game_setup_service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=4, full_name="A"
    )

    roster = game_setup_service.list_roster(game.id, team.id)

    assert [entry.jersey_number for entry in roster] == [4, 27]


# -- get_game ---------------------------------------------------------------


def test_get_game_returns_the_game(game_setup_service):
    game = game_setup_service.create_game()
    assert game_setup_service.get_game(game.id).id == game.id


def test_get_game_raises_for_a_missing_game(game_setup_service):
    with pytest.raises(KeyError):
        game_setup_service.get_game(999999)


# -- home/away team: nullable until picked, correctable afterward -----------


def test_a_new_game_has_no_home_or_away_team(game_setup_service):
    game = game_setup_service.create_game()
    assert game.home_team_id is None
    assert game.away_team_id is None


def test_set_side_team_sets_home(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")

    game_setup_service.set_side_team(game.id, "home", team.id)

    assert game_setup_service.get_game(game.id).home_team_id == team.id


def test_set_side_team_sets_away(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Rivals")

    game_setup_service.set_side_team(game.id, "away", team.id)

    assert game_setup_service.get_game(game.id).away_team_id == team.id


def test_set_side_team_rejects_the_same_team_on_both_sides(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    game_setup_service.set_side_team(game.id, "home", team.id)

    with pytest.raises(SameTeamBothSidesError):
        game_setup_service.set_side_team(game.id, "away", team.id)

    assert game_setup_service.get_game(game.id).away_team_id is None


def test_set_side_team_is_correctable_afterward_unlike_rink_type(game_setup_service):
    # No freeze-after-creation treatment for home/away (see CONTEXT.md's
    # Game entry) -- fixing a wrong pick is a plain update, any time.
    game = game_setup_service.create_game()
    first_choice = game_setup_service.create_team("Icebreakers")
    corrected_choice = game_setup_service.create_team("Real Home Team")
    game_setup_service.set_side_team(game.id, "home", first_choice.id)

    game_setup_service.set_side_team(game.id, "home", corrected_choice.id)

    assert game_setup_service.get_game(game.id).home_team_id == corrected_choice.id


# -- video_path: attached on demand, not required at creation ---------------


def test_a_new_game_has_no_video_path(game_setup_service):
    game = game_setup_service.create_game()
    assert game.video_path is None


def test_set_video_path_attaches_the_path(game_setup_service):
    game = game_setup_service.create_game()

    game_setup_service.set_video_path(game.id, "C:/clips/game.mp4")

    assert game_setup_service.get_game(game.id).video_path == "C:/clips/game.mp4"


# -- list_games: most-recently-worked-on first -------------------------------


def test_list_games_orders_by_most_recently_updated_first(game_setup_service):
    older = game_setup_service.create_game()
    newer = game_setup_service.create_game()

    assert [game.id for game in game_setup_service.list_games()] == [newer.id, older.id]


def test_list_games_ordering_bumps_on_roster_activity_not_just_game_row_edits(
    game_setup_service,
):
    first = game_setup_service.create_game()
    second = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    assert [game.id for game in game_setup_service.list_games()] == [
        second.id,
        first.id,
    ]

    # Touching the older game (roster activity, not a Game-row edit) should
    # bump it back to the top.
    game_setup_service.add_roster_entry(
        game_id=first.id, team_id=team.id, jersey_number=9, full_name="Player"
    )

    assert [game.id for game in game_setup_service.list_games()] == [
        first.id,
        second.id,
    ]


def test_list_games_ordering_bumps_on_event_activity(game_setup_service, session):
    first = game_setup_service.create_game()
    second = game_setup_service.create_game()

    session.add(
        Stoppage(
            game_id=first.id,
            video_timestamp=1000,
            source=EventSource.MANUAL,
            confirmed=False,
        )
    )
    session.commit()

    assert [game.id for game in game_setup_service.list_games()] == [
        first.id,
        second.id,
    ]


def test_list_games_ordering_bumps_on_event_deletion_too(game_setup_service, session):
    first = game_setup_service.create_game()
    second = game_setup_service.create_game()
    stoppage = Stoppage(
        game_id=first.id,
        video_timestamp=1000,
        source=EventSource.MANUAL,
        confirmed=False,
    )
    session.add(stoppage)
    session.commit()
    # Touch `second` more recently than the stoppage tagged against `first` above.
    game_setup_service.set_video_path(second.id, "C:/clips/second.mp4")
    assert [game.id for game in game_setup_service.list_games()] == [
        second.id,
        first.id,
    ]

    # Deleting an event is still "activity" on its game -- should bump
    # `first` back above `second`, which hasn't been touched since.
    session.delete(stoppage)
    session.commit()

    assert [game.id for game in game_setup_service.list_games()] == [
        first.id,
        second.id,
    ]


# -- unit assignments (ticket 17) -------------------------------------------


def _rostered_player(game_setup_service, game, team, jersey_number):
    return game_setup_service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=jersey_number
    ).player_id


def test_assign_unit_persists_a_game_unit_assignment(game_setup_service, session):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, game, team, 14)

    assignment = game_setup_service.assign_unit(
        game_id=game.id,
        team_id=team.id,
        player_id=player_id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=1,
    )

    assert session.get(GameUnitAssignment, assignment.id) is not None
    assert assignment.unit_type is UnitType.FORWARD_LINE
    assert assignment.unit_number == 1


def test_a_player_can_hold_several_unit_types_at_once(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, game, team, 14)

    for unit_type, unit_number in (
        (UnitType.FORWARD_LINE, 1),
        (UnitType.POWER_PLAY, 1),
        (UnitType.PENALTY_KILL, 2),
    ):
        game_setup_service.assign_unit(
            game_id=game.id,
            team_id=team.id,
            player_id=player_id,
            unit_type=unit_type,
            unit_number=unit_number,
        )

    assert game_setup_service.player_unit_assignments(game.id, player_id) == {
        UnitType.FORWARD_LINE: 1,
        UnitType.POWER_PLAY: 1,
        UnitType.PENALTY_KILL: 2,
    }


def test_assign_unit_again_for_the_same_type_replaces_the_unit_number(
    game_setup_service, session
):
    # At most one assignment per (game, player, unit_type) -- reassigning
    # moves the player rather than raising or duplicating.
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, game, team, 14)
    kwargs = {"game_id": game.id, "team_id": team.id, "player_id": player_id}

    game_setup_service.assign_unit(
        **kwargs, unit_type=UnitType.FORWARD_LINE, unit_number=1
    )
    game_setup_service.assign_unit(
        **kwargs, unit_type=UnitType.FORWARD_LINE, unit_number=3
    )

    assert game_setup_service.player_unit_assignments(game.id, player_id) == {
        UnitType.FORWARD_LINE: 3
    }
    assert session.query(GameUnitAssignment).count() == 1


def test_unassign_unit_removes_only_that_unit_type(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, game, team, 14)
    kwargs = {"game_id": game.id, "team_id": team.id, "player_id": player_id}
    game_setup_service.assign_unit(
        **kwargs, unit_type=UnitType.FORWARD_LINE, unit_number=1
    )
    game_setup_service.assign_unit(
        **kwargs, unit_type=UnitType.POWER_PLAY, unit_number=1
    )

    game_setup_service.unassign_unit(
        game_id=game.id, player_id=player_id, unit_type=UnitType.POWER_PLAY
    )

    assert game_setup_service.player_unit_assignments(game.id, player_id) == {
        UnitType.FORWARD_LINE: 1
    }


def test_unassign_unit_is_a_no_op_when_nothing_is_assigned(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, game, team, 14)

    game_setup_service.unassign_unit(
        game_id=game.id, player_id=player_id, unit_type=UnitType.POWER_PLAY
    )

    assert game_setup_service.player_unit_assignments(game.id, player_id) == {}


def test_assign_unit_rejects_a_player_not_on_that_teams_roster(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    other_team = game_setup_service.create_team("Rivals")
    player_id = _rostered_player(game_setup_service, game, other_team, 14)

    with pytest.raises(PlayerNotRosteredError):
        game_setup_service.assign_unit(
            game_id=game.id,
            team_id=team.id,
            player_id=player_id,
            unit_type=UnitType.FORWARD_LINE,
            unit_number=1,
        )


def test_assign_unit_rejects_a_non_positive_unit_number(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, game, team, 14)

    with pytest.raises(ValueError):
        game_setup_service.assign_unit(
            game_id=game.id,
            team_id=team.id,
            player_id=player_id,
            unit_type=UnitType.FORWARD_LINE,
            unit_number=0,
        )


def test_unit_assignments_are_per_game(game_setup_service):
    # Fixed for the whole game, but a different game starts fresh.
    game = game_setup_service.create_game()
    other_game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, game, team, 14)
    game_setup_service.add_roster_entry(
        game_id=other_game.id, team_id=team.id, jersey_number=14, player_id=player_id
    )
    game_setup_service.assign_unit(
        game_id=game.id,
        team_id=team.id,
        player_id=player_id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=1,
    )

    assert game_setup_service.player_unit_assignments(other_game.id, player_id) == {}


def test_list_games_ordering_bumps_on_unit_assignment_activity(game_setup_service):
    first = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, first, team, 14)
    second = game_setup_service.create_game()
    assert game_setup_service.list_games()[0].id == second.id

    game_setup_service.assign_unit(
        game_id=first.id,
        team_id=team.id,
        player_id=player_id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=1,
    )

    assert game_setup_service.list_games()[0].id == first.id


def test_set_player_units_assigns_moves_and_unassigns_in_one_call(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    player_id = _rostered_player(game_setup_service, game, team, 14)
    kwargs = {"game_id": game.id, "team_id": team.id, "player_id": player_id}
    game_setup_service.set_player_units(
        **kwargs, units={UnitType.FORWARD_LINE: 1, UnitType.PENALTY_KILL: 1}
    )

    game_setup_service.set_player_units(
        **kwargs,
        units={
            UnitType.FORWARD_LINE: 2,
            UnitType.POWER_PLAY: 1,
            UnitType.PENALTY_KILL: None,
        },
    )

    assert game_setup_service.player_unit_assignments(game.id, player_id) == {
        UnitType.FORWARD_LINE: 2,
        UnitType.POWER_PLAY: 1,
    }


def test_unit_numbers_maps_each_player_on_a_unit_type_to_their_number(
    game_setup_service,
):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    line_1 = [_rostered_player(game_setup_service, game, team, n) for n in (14, 17)]
    line_2 = _rostered_player(game_setup_service, game, team, 9)
    for player_id, number in ((line_1[0], 1), (line_1[1], 1), (line_2, 2)):
        game_setup_service.assign_unit(
            game_id=game.id,
            team_id=team.id,
            player_id=player_id,
            unit_type=UnitType.FORWARD_LINE,
            unit_number=number,
        )

    game_setup_service.assign_unit(
        game_id=game.id,
        team_id=team.id,
        player_id=line_2,
        unit_type=UnitType.POWER_PLAY,
        unit_number=1,
    )

    numbers = game_setup_service.unit_numbers(
        game_id=game.id, team_id=team.id, unit_type=UnitType.FORWARD_LINE
    )

    assert numbers == {line_1[0]: 1, line_1[1]: 1, line_2: 2}


def test_unit_numbers_is_scoped_to_one_team(game_setup_service):
    game = game_setup_service.create_game()
    team = game_setup_service.create_team("Icebreakers")
    other = game_setup_service.create_team("Rivals")
    player_id = _rostered_player(game_setup_service, game, other, 14)
    game_setup_service.assign_unit(
        game_id=game.id,
        team_id=other.id,
        player_id=player_id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=1,
    )

    assert (
        game_setup_service.unit_numbers(
            game_id=game.id, team_id=team.id, unit_type=UnitType.FORWARD_LINE
        )
        == {}
    )


# -- on_save: telling the window a save happened (#64) ------------------------


def test_unit_saves_report_units_changed(session):
    reasons: list[str] = []
    service = GameSetupService(session, on_save=reasons.append)
    game = service.create_game()
    team = service.create_team("Icebreakers")
    entry = service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=14)
    reasons.clear()

    service.assign_unit(
        game_id=game.id,
        team_id=team.id,
        player_id=entry.player_id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=1,
    )
    service.unassign_unit(
        game_id=game.id, player_id=entry.player_id, unit_type=UnitType.FORWARD_LINE
    )

    assert reasons == ["units changed", "units changed"]


def test_each_kind_of_setup_save_has_its_own_reason(session):
    reasons: list[str] = []
    service = GameSetupService(session, on_save=reasons.append)

    game = service.create_game()
    team = service.create_team("Icebreakers")
    service.set_side_team(game.id, "home", team.id)
    player = service.create_player(full_name="Jordan Kim")
    service.set_player_full_name(player.id, "Jordan Kimball")
    service.set_player_position(player.id, Position.CENTER)
    service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=14, player_id=player.id
    )

    assert reasons == [
        "game created",
        "team created",
        "game setup changed",
        "player created",
        "player changed",
        "player changed",
        "roster changed",
    ]


def test_attaching_footage_is_not_reported_as_a_setup_save(session):
    reasons: list[str] = []
    service = GameSetupService(session, on_save=reasons.append)
    game = service.create_game()
    reasons.clear()

    service.set_video_path(game.id, "C:/footage/game.mp4")

    assert reasons == []


def test_unassigning_a_unit_nobody_holds_is_not_a_save(session):
    reasons: list[str] = []
    service = GameSetupService(session, on_save=reasons.append)
    game = service.create_game()
    reasons.clear()

    service.unassign_unit(game_id=game.id, player_id=1, unit_type=UnitType.POWER_PLAY)

    assert reasons == []


# -- correcting a roster entry after the fact (#89) ------------------------


def _shot(session, game, *, at, shooter, assist1=None):
    shot = ShotAttempt(
        game_id=game.id,
        video_timestamp=at,
        shooter_id=shooter,
        assist1_id=assist1,
        shot_type=ShotType.WRIST,
        shot_outcome=ShotOutcome.SAVED,
    )
    session.add(shot)
    session.commit()
    return shot


def _roster_of_two(service):
    """A game whose team has a mistyped, nameless #41 and a real #14 Smith."""
    game = service.create_game()
    team = service.create_team("Icebreakers")
    stray = service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=41)
    smith = service.add_roster_entry(
        game_id=game.id, team_id=team.id, jersey_number=14, full_name="Smith"
    )
    return game, team, stray, smith


def _jersey_numbers(service, game, team):
    return [entry.jersey_number for entry in service.list_roster(game.id, team.id)]


def test_renumber_roster_entry_changes_the_jersey_number(game_setup_service):
    game, team, stray, _smith = _roster_of_two(game_setup_service)

    game_setup_service.renumber_roster_entry(stray.id, 15)

    assert _jersey_numbers(game_setup_service, game, team) == [14, 15]


def test_a_renumbered_entrys_events_still_name_the_same_player(
    game_setup_service, session
):
    game, _team, stray, _smith = _roster_of_two(game_setup_service)
    shot = _shot(session, game, at=1000, shooter=stray.player_id)

    game_setup_service.renumber_roster_entry(stray.id, 15)

    assert shot.shooter_id == stray.player_id
    assert stray.jersey_number == 15


def test_renumber_onto_a_taken_number_is_rejected(game_setup_service):
    _game, team, stray, _smith = _roster_of_two(game_setup_service)

    with pytest.raises(DuplicateJerseyNumberError) as error:
        game_setup_service.renumber_roster_entry(stray.id, 14)

    assert error.value.team_id == team.id
    assert error.value.jersey_number == 14
    assert stray.jersey_number == 41


def test_renumber_to_its_own_number_is_not_a_save(session):
    reasons: list[str] = []
    service = GameSetupService(session, on_save=reasons.append)
    _game, _team, stray, _smith = _roster_of_two(service)
    reasons.clear()

    service.renumber_roster_entry(stray.id, 41)

    assert reasons == []


def test_find_roster_entry_looks_up_a_jersey_number(game_setup_service):
    game, team, _stray, smith = _roster_of_two(game_setup_service)

    assert game_setup_service.find_roster_entry(game.id, team.id, 14) == smith
    assert game_setup_service.find_roster_entry(game.id, team.id, 99) is None


def test_referencing_events_lists_every_role_in_video_order(
    game_setup_service, session
):
    game, team, stray, smith = _roster_of_two(game_setup_service)
    other_game = game_setup_service.create_game()
    shift = ShiftChange(
        game_id=game.id,
        video_timestamp=3000,
        shift_team_id=team.id,
        shift_player_id=stray.player_id,
        shift_on_ice=True,
    )
    faceoff = Faceoff(
        game_id=game.id,
        video_timestamp=2000,
        faceoff_home_participant_id=stray.player_id,
        faceoff_away_participant_unknown=True,
    )
    penalty = Penalty(
        game_id=game.id,
        video_timestamp=500,
        penalty_team_id=team.id,
        penalty_player_id=stray.player_id,
    )
    elsewhere = Penalty(
        game_id=other_game.id,
        video_timestamp=100,
        penalty_team_id=team.id,
        penalty_player_id=stray.player_id,
    )
    session.add_all([shift, faceoff, penalty, elsewhere])
    session.commit()
    assist = _shot(
        session, game, at=4000, shooter=smith.player_id, assist1=stray.player_id
    )
    _shot(session, game, at=5000, shooter=smith.player_id)

    events = game_setup_service.referencing_events(game.id, stray.player_id)

    assert events == [penalty, faceoff, shift, assist]


def test_remove_roster_entry_deletes_it_and_its_unit_assignments(game_setup_service):
    game, team, stray, smith = _roster_of_two(game_setup_service)
    for entry in (stray, smith):
        game_setup_service.assign_unit(
            game_id=game.id,
            team_id=team.id,
            player_id=entry.player_id,
            unit_type=UnitType.FORWARD_LINE,
            unit_number=1,
        )
    stray_player_id = stray.player_id

    game_setup_service.remove_roster_entry(stray.id)

    assert _jersey_numbers(game_setup_service, game, team) == [14]
    assert game_setup_service.player_unit_assignments(game.id, stray_player_id) == {}
    assert game_setup_service.player_unit_assignments(game.id, smith.player_id) == {
        UnitType.FORWARD_LINE: 1
    }


def test_removing_a_players_only_roster_entry_deletes_the_player(
    game_setup_service, session
):
    _game, _team, stray, _smith = _roster_of_two(game_setup_service)
    stray_player_id = stray.player_id

    game_setup_service.remove_roster_entry(stray.id)

    assert session.get(Player, stray_player_id) is None
    assert stray_player_id not in [p.id for p in game_setup_service.list_players()]


def test_removing_an_entry_keeps_a_player_rostered_in_another_game(
    game_setup_service, session
):
    _game, team, stray, _smith = _roster_of_two(game_setup_service)
    other_game = game_setup_service.create_game()
    game_setup_service.add_roster_entry(
        game_id=other_game.id,
        team_id=team.id,
        jersey_number=41,
        player_id=stray.player_id,
    )
    stray_player_id = stray.player_id

    game_setup_service.remove_roster_entry(stray.id)

    assert session.get(Player, stray_player_id) is not None
    assert _jersey_numbers(game_setup_service, other_game, team) == [41]


def test_removing_a_referenced_entry_is_refused_listing_the_events(
    game_setup_service, session
):
    game, team, stray, _smith = _roster_of_two(game_setup_service)
    shot = _shot(session, game, at=754_000, shooter=stray.player_id)

    with pytest.raises(RosterEntryInUseError) as error:
        game_setup_service.remove_roster_entry(stray.id)

    assert error.value.events == [shot]
    assert str(error.value) == (
        "#41 is used by 1 event — change it first: shot attempt @ 0:12:34"
    )
    assert _jersey_numbers(game_setup_service, game, team) == [14, 41]


def test_the_refusal_lists_several_events(game_setup_service, session):
    game, _team, stray, _smith = _roster_of_two(game_setup_service)
    _shot(session, game, at=1000, shooter=stray.player_id)
    _shot(session, game, at=2000, shooter=stray.player_id)

    with pytest.raises(RosterEntryInUseError) as error:
        game_setup_service.remove_roster_entry(stray.id)

    assert str(error.value) == (
        "#41 is used by 2 events — change them first: "
        "shot attempt @ 0:00:01, shot attempt @ 0:00:02"
    )


def test_merge_moves_every_reference_to_the_target_and_removes_the_source(
    game_setup_service, session
):
    game, team, stray, smith = _roster_of_two(game_setup_service)
    shot = _shot(session, game, at=1000, shooter=stray.player_id)
    faceoff = Faceoff(
        game_id=game.id,
        video_timestamp=2000,
        faceoff_home_participant_id=stray.player_id,
        faceoff_away_participant_unknown=True,
    )
    shift = ShiftChange(
        game_id=game.id,
        video_timestamp=3000,
        shift_team_id=team.id,
        shift_player_id=stray.player_id,
        shift_on_ice=True,
    )
    session.add_all([faceoff, shift])
    session.commit()
    game_setup_service.assign_unit(
        game_id=game.id,
        team_id=team.id,
        player_id=stray.player_id,
        unit_type=UnitType.POWER_PLAY,
        unit_number=1,
    )
    game_setup_service.assign_unit(
        game_id=game.id,
        team_id=team.id,
        player_id=smith.player_id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=2,
    )
    stray_player_id = stray.player_id

    game_setup_service.merge_roster_entry(stray.id, smith.id)

    assert shot.shooter_id == smith.player_id
    assert shot.shooter_unknown is False
    assert faceoff.faceoff_home_participant_id == smith.player_id
    assert faceoff.faceoff_away_participant_unknown is True
    assert shift.shift_player_id == smith.player_id
    assert _jersey_numbers(game_setup_service, game, team) == [14]
    assert session.get(Player, stray_player_id) is None
    assert game_setup_service.player_unit_assignments(game.id, smith.player_id) == {
        UnitType.FORWARD_LINE: 2
    }


def test_merge_leaves_the_source_players_events_in_other_games_alone(
    game_setup_service, session
):
    _game, team, stray, smith = _roster_of_two(game_setup_service)
    other_game = game_setup_service.create_game()
    game_setup_service.add_roster_entry(
        game_id=other_game.id,
        team_id=team.id,
        jersey_number=41,
        player_id=stray.player_id,
    )
    stray_player_id = stray.player_id
    elsewhere = _shot(session, other_game, at=1000, shooter=stray_player_id)

    game_setup_service.merge_roster_entry(stray.id, smith.id)

    assert elsewhere.shooter_id == stray_player_id
    assert session.get(Player, stray_player_id) is not None


def test_merge_is_refused_while_an_event_names_both_players(
    game_setup_service, session
):
    game, team, stray, smith = _roster_of_two(game_setup_service)
    alone = _shot(session, game, at=1000, shooter=stray.player_id)
    both = _shot(
        session, game, at=754_000, shooter=stray.player_id, assist1=smith.player_id
    )

    with pytest.raises(SharedEventsError) as error:
        game_setup_service.merge_roster_entry(stray.id, smith.id)

    assert error.value.events == [both]
    assert str(error.value) == (
        "#41 and #14 Smith are both on 1 event — change it first: "
        "shot attempt @ 0:12:34"
    )
    assert _jersey_numbers(game_setup_service, game, team) == [14, 41]
    assert alone.shooter_id == stray.player_id


def test_merge_preview_lists_the_events_a_merge_would_move_without_writing(
    game_setup_service, session
):
    game, team, stray, smith = _roster_of_two(game_setup_service)
    shot = _shot(session, game, at=1000, shooter=stray.player_id)
    _shot(session, game, at=2000, shooter=smith.player_id)

    assert game_setup_service.merge_preview(stray.id, smith.id) == [shot]
    assert _jersey_numbers(game_setup_service, game, team) == [14, 41]


def test_merge_preview_refuses_like_the_merge_itself(game_setup_service, session):
    game, _team, stray, smith = _roster_of_two(game_setup_service)
    _shot(session, game, at=1000, shooter=stray.player_id, assist1=smith.player_id)

    with pytest.raises(SharedEventsError):
        game_setup_service.merge_preview(stray.id, smith.id)


def test_check_removable_refuses_like_the_removal_itself(game_setup_service, session):
    game, team, stray, smith = _roster_of_two(game_setup_service)
    _shot(session, game, at=1000, shooter=stray.player_id)

    with pytest.raises(RosterEntryInUseError):
        game_setup_service.check_removable(stray.id)
    game_setup_service.check_removable(smith.id)
    assert _jersey_numbers(game_setup_service, game, team) == [14, 41]


def test_merge_rejects_entries_from_different_teams(game_setup_service):
    game, _team, stray, _smith = _roster_of_two(game_setup_service)
    rivals = game_setup_service.create_team("Rivals")
    opponent = game_setup_service.add_roster_entry(
        game_id=game.id, team_id=rivals.id, jersey_number=14
    )

    with pytest.raises(ValueError):
        game_setup_service.merge_roster_entry(stray.id, opponent.id)


def test_roster_corrections_report_roster_changed(session):
    reasons: list[str] = []
    service = GameSetupService(session, on_save=reasons.append)
    game, team, stray, smith = _roster_of_two(service)
    extra = service.add_roster_entry(game_id=game.id, team_id=team.id, jersey_number=9)
    reasons.clear()

    service.renumber_roster_entry(stray.id, 15)
    service.merge_roster_entry(stray.id, smith.id)
    service.remove_roster_entry(extra.id)

    assert reasons == ["roster changed", "roster changed", "roster changed"]
