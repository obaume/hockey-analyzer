"""`load_game_data` (ticket 18): the one database read StatsEngine's
input comes from -- a game tagged through `TaggingSession` comes back
out as a `GameData` the engine can compute over."""

from __future__ import annotations

import pytest

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import EventType, Position, ShotOutcome, ShotType
from hockey_analyzer.domain.game_data import load_game_data


def test_a_tagged_game_loads_into_stats(session, game_and_teams, tagging_session):
    game, home, away = game_and_teams
    game.home_team_id, game.away_team_id = home.id, away.id
    kim = tagging_session.resolve_or_create_roster_entry(home.id, 14)
    goalie = tagging_session.resolve_or_create_roster_entry(away.id, 35)
    goalie.player.position = Position.GOALIE

    tagging_session.log_ad_hoc_line_change("home", [14], 1000, on_ice=True)
    tagging_session.log_ad_hoc_line_change("away", [35], 1000, on_ice=True)
    shot = tagging_session.log_event(
        EventType.SHOT_ATTEMPT,
        2000,
        strength_state="5v5",
        shot_outcome=ShotOutcome.GOAL,
        shot_type=ShotType.WRIST,
    )
    tagging_session.set_player_reference(
        shot.id, "home", reference="shooter", jersey_number=14
    )

    data = load_game_data(session, game.id)

    assert [event.video_timestamp for event in data.events] == [1000, 1000, 2000]
    home_stats = stats_engine.team_stats(data, home.id)
    assert home_stats.goals.for_ == 1
    (kim_stats,) = stats_engine.skater_stats(data).skaters
    assert (kim_stats.player_id, kim_stats.plus_minus) == (kim.player_id, 1)
    (goalie_stats,) = stats_engine.goalie_stats(data)
    assert goalie_stats.save_percentage == pytest.approx(0.0)


def test_loading_an_unknown_game_raises(session):
    with pytest.raises(KeyError):
        load_game_data(session, 999)
