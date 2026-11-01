"""Official events at league import, and refreshing an older game from the
league (see CONTEXT.md's Official event): the league's play-by-play is
stored with the game, never as an `Event`, and a refresh replaces it and
backfills licence numbers without touching score, roster or units."""

from __future__ import annotations

import pytest
from sihf_fixtures import (
    GAME_ID,
    GAME_LINK,
    FakeLeagueSource,
    Foul,
    Goal,
    GoalieChange,
    ShootoutShot,
    sihf_game_overview,
)
from sqlalchemy import func, select

from hockey_analyzer.domain import stats_engine
from hockey_analyzer.domain.enums import OfficialEventKind, Side
from hockey_analyzer.domain.game_data import load_game_data
from hockey_analyzer.domain.models import (
    Event,
    Game,
    GameRosterEntry,
    GameUnitAssignment,
    OfficialEvent,
    OfficialPlayer,
    Player,
    Team,
)
from hockey_analyzer.league_import import (
    ImportResolution,
    LeagueImportService,
    LeagueSourceError,
    RefreshSkipReason,
)

# Licence numbers the fixture builder hands out in listing order (see
# `sihf_game_overview`): home #69 Mémeteau, #6 Métraux, #11 Würth, #39
# Keller, #41 Meyer; away #15 Brogli, #21 Galley.
MEMETEAU, METRAUX, WURTH = "300011", "300007", "300009"
KELLER, MEYER = "300001", "300002"
BROGLI, GALLEY = "300016", "300018"


def _timeline():
    return [
        [
            Goal("home", "08:22", 69, assists=(6, 11)),
            Foul("away", "13:45", 15, minutes=2, name="Cross-Check"),
        ],
        [
            GoalieChange("home", "36:24", 39, going_in=False),
            GoalieChange("home", "36:24", 41, going_in=True),
            Goal("home", "26:47", 69, tags="PP1"),
        ],
        [],
        [Goal("away", "65:56", 21, tags="EQ,GWG")],
    ]


def _body(**overrides):
    return sihf_game_overview(
        timeline=_timeline(),
        shootout=[ShootoutShot("home", 69, 30, scored=True)],
        **overrides,
    )


def _import(session, body=None):
    service = LeagueImportService(session, FakeLeagueSource(body or _body()))
    proposal = service.propose(GAME_LINK)
    return service.confirm(proposal, ImportResolution.as_proposed(proposal))


def _official(session, game):
    return list(
        session.scalars(
            select(OfficialEvent)
            .where(OfficialEvent.game_id == game.id)
            .order_by(OfficialEvent.period_number, OfficialEvent.elapsed_seconds)
        )
    )


def _summary(entry):
    return (
        entry.kind,
        entry.period_number,
        entry.elapsed_seconds,
        entry.side,
        entry.player_league_id,
    )


# -- import ---------------------------------------------------------------------


def test_import_stores_goals_penalties_and_goalie_changes(session):
    game = _import(session)

    assert [_summary(entry) for entry in _official(session, game)] == [
        (OfficialEventKind.GOAL, 1, 502, Side.HOME, MEMETEAU),
        (OfficialEventKind.PENALTY, 1, 825, Side.AWAY, BROGLI),
        (OfficialEventKind.GOAL, 2, 407, Side.HOME, MEMETEAU),
        (OfficialEventKind.GOALIE_CHANGE, 2, 984, Side.HOME, KELLER),
        (OfficialEventKind.GOALIE_CHANGE, 2, 984, Side.HOME, MEYER),
        (OfficialEventKind.GOAL, 4, 356, Side.AWAY, GALLEY),
    ]


def test_a_goal_carries_scorer_assists_and_strength_tag(session):
    goal = _official(session, _import(session))[0]

    assert goal.player == OfficialPlayer(MEMETEAU, "Mémeteau Matthias", 69)
    assert goal.assist1 == OfficialPlayer(METRAUX, "Métraux Dorian", 6)
    assert goal.assist2 == OfficialPlayer(WURTH, "Würth Ezequiel", 11)
    assert goal.strength_tag == "EQ"


def test_an_unassisted_goal_names_no_assists(session):
    power_play_goal = _official(session, _import(session))[2]

    assert power_play_goal.assist1 is None
    assert power_play_goal.assist2 is None
    assert power_play_goal.strength_tag == "PP1"


def test_a_goal_keeps_every_tag_the_league_prints(session):
    # Strength first, then the rest: an empty-net tag isn't first, so
    # keeping the first alone would lose it.
    body = sihf_game_overview(
        timeline=[
            [
                Goal("home", "19:31", 69, tags="EQ,EN"),
                Goal("away", "19:50", 21, tags="PP1,GWG"),
            ]
        ]
    )
    empty_net, game_winner = _official(session, _import(session, body))

    assert empty_net.strength_tag == "EQ,EN"
    assert game_winner.strength_tag == "PP1,GWG"


def test_a_penalty_carries_its_name_and_minutes(session):
    penalty = _official(session, _import(session))[1]

    assert (penalty.penalty_name, penalty.penalty_minutes) == ("Cross-Check", 2)
    assert penalty.player == OfficialPlayer(BROGLI, "Brogli Gaël", 15)


def test_a_goalie_change_says_which_way(session):
    out, in_ = _official(session, _import(session))[3:5]

    assert (out.player_league_id, out.goalie_in) == (KELLER, False)
    assert (in_.player_league_id, in_.goalie_in) == (MEYER, True)


def test_shootout_attempts_are_not_stored(session):
    entries = _official(session, _import(session))

    assert all(entry.period_number <= 4 for entry in entries)
    assert len(entries) == 6


def test_official_events_are_never_tagged_events(session):
    game = _import(session)

    assert session.scalar(select(func.count()).select_from(Event)) == 0
    assert load_game_data(session, game.id).events == []


def test_an_imported_game_shows_its_official_events_in_game_data(session):
    game = _import(session)

    data = load_game_data(session, game.id)

    assert [_summary(entry) for entry in data.official_events] == [
        _summary(entry) for entry in _official(session, game)
    ]


def test_stats_are_unchanged_by_official_events(session):
    game = _import(session)
    data = load_game_data(session, game.id)
    without = type(data)(
        game=data.game,
        events=data.events,
        roster=data.roster,
        unit_assignments=data.unit_assignments,
    )

    assert data.official_events
    assert stats_engine.team_stats(data, game.home_team_id) == (
        stats_engine.team_stats(without, game.home_team_id)
    )
    assert stats_engine.skater_stats(data) == stats_engine.skater_stats(without)
    assert stats_engine.goalie_stats(data) == stats_engine.goalie_stats(without)


def test_a_game_without_a_timeline_imports_no_official_events(session):
    game = _import(session, sihf_game_overview())

    assert _official(session, game) == []


# -- refresh from the league ----------------------------------------------------


@pytest.fixture
def older_game(session):
    """A game imported before Official events and licence numbers existed:
    linked to its league game, rostered by hand-corrected jersey numbers,
    none of its players carrying a licence number."""
    service = LeagueImportService(session, FakeLeagueSource(sihf_game_overview()))
    proposal = service.propose(GAME_LINK)
    game = service.confirm(proposal, ImportResolution.as_proposed(proposal))
    for player in session.scalars(select(Player)):
        player.league_id = None
    session.commit()
    return game


def _refresh(session, game, body=None):
    source = FakeLeagueSource(body or _body())
    return LeagueImportService(session, source).refresh(game.id), source


def _player_on(session, game, side, jersey):
    team_id = game.home_team_id if side == Side.HOME else game.away_team_id
    return session.scalar(
        select(Player)
        .join(GameRosterEntry)
        .where(
            GameRosterEntry.game_id == game.id,
            GameRosterEntry.team_id == team_id,
            GameRosterEntry.jersey_number == jersey,
        )
    )


def test_refresh_fetches_the_games_own_league_game(session, older_game):
    _, source = _refresh(session, older_game)

    assert source.requested_game_ids == [GAME_ID]


def test_refresh_fills_the_official_events(session, older_game):
    report, _ = _refresh(session, older_game)

    assert len(_official(session, older_game)) == 6
    assert report.official_events == 6


def test_refresh_gives_rostered_players_their_licence_numbers_by_jersey(
    session, older_game
):
    report, _ = _refresh(session, older_game)

    assert _player_on(session, older_game, Side.HOME, 69).league_id == MEMETEAU
    assert _player_on(session, older_game, Side.AWAY, 21).league_id == GALLEY
    roster_size = session.scalar(
        select(func.count())
        .select_from(GameRosterEntry)
        .where(GameRosterEntry.game_id == older_game.id)
    )
    assert report.licences_added == roster_size
    assert report.skipped == ()


def test_refreshing_twice_replaces_the_official_events(session, older_game):
    _refresh(session, older_game)
    timeline = _timeline()
    timeline[0].append(Goal("away", "15:00", 21))
    report, _ = _refresh(session, older_game, sihf_game_overview(timeline=timeline))

    assert len(_official(session, older_game)) == 7
    assert report.official_events == 7
    assert report.licences_added == 0


def test_refresh_never_touches_score_roster_or_units(session, older_game):
    older_game.home_score, older_game.away_score = 2, 3
    older_game.period_scores = [{"home": 2, "away": 3}]
    home_69 = _player_on(session, older_game, Side.HOME, 69)
    for assignment in session.scalars(
        select(GameUnitAssignment).where(GameUnitAssignment.player_id == home_69.id)
    ):
        session.delete(assignment)
    session.commit()

    def snapshot():
        return (
            sorted(
                (e.team_id, e.jersey_number, e.player_id, e.position)
                for e in session.scalars(
                    select(GameRosterEntry).where(
                        GameRosterEntry.game_id == older_game.id
                    )
                )
            ),
            sorted(
                (a.player_id, a.unit_type, a.unit_number)
                for a in session.scalars(
                    select(GameUnitAssignment).where(
                        GameUnitAssignment.game_id == older_game.id
                    )
                )
            ),
        )

    before = snapshot()
    _refresh(session, older_game)

    session.expire_all()
    game = session.get(Game, older_game.id)
    assert (game.home_score, game.away_score) == (2, 3)
    assert game.period_scores == [{"home": 2, "away": 3}]
    # Including #69's hand-removed forward line: not prefilled again.
    assert snapshot() == before
    assert home_69.id not in {player for player, _, _ in before[1]}


def test_a_player_with_a_different_licence_number_is_skipped_and_reported(
    session, older_game
):
    player = _player_on(session, older_game, Side.HOME, 69)
    player.league_id = "999999"
    session.commit()

    report, _ = _refresh(session, older_game)

    assert player.league_id == "999999"
    ((skipped),) = report.skipped
    assert (skipped.side, skipped.jersey_number, skipped.league_id) == (
        Side.HOME,
        69,
        MEMETEAU,
    )
    assert skipped.reason == RefreshSkipReason.HAS_ANOTHER_NUMBER


def test_a_player_whose_number_another_player_holds_is_skipped_and_reported(
    session, older_game
):
    session.add(Player(full_name="Mémeteau Matthias", league_id=MEMETEAU))
    session.commit()
    player = _player_on(session, older_game, Side.HOME, 69)

    report, _ = _refresh(session, older_game)

    assert player.league_id is None
    ((skipped),) = report.skipped
    assert (skipped.side, skipped.jersey_number) == (Side.HOME, 69)
    assert skipped.reason == RefreshSkipReason.NUMBER_HELD_BY_ANOTHER_PLAYER


def test_a_player_already_holding_their_number_is_neither_added_nor_skipped(
    session, older_game
):
    _player_on(session, older_game, Side.HOME, 69).league_id = MEMETEAU
    session.commit()

    report, _ = _refresh(session, older_game)

    assert report.skipped == ()
    assert _player_on(session, older_game, Side.HOME, 69).league_id == MEMETEAU


def test_a_lineup_player_not_on_the_roster_is_left_alone(session, older_game):
    entry = session.scalar(
        select(GameRosterEntry).where(
            GameRosterEntry.game_id == older_game.id,
            GameRosterEntry.team_id == older_game.home_team_id,
            GameRosterEntry.jersey_number == 69,
        )
    )
    entry.jersey_number = 96
    session.commit()

    report, _ = _refresh(session, older_game)

    assert _player_on(session, older_game, Side.HOME, 96).league_id is None
    assert report.skipped == ()


def test_a_failed_fetch_writes_nothing(session, older_game):
    _refresh(session, older_game)
    _player_on(session, older_game, Side.HOME, 69).league_id = None
    session.commit()

    with pytest.raises(LeagueSourceError):
        _refresh(session, older_game, LeagueSourceError("league is down"))

    assert len(_official(session, older_game)) == 6
    assert _player_on(session, older_game, Side.HOME, 69).league_id is None


def test_a_body_for_another_game_is_refused_and_writes_nothing(session, older_game):
    with pytest.raises(LeagueSourceError):
        _refresh(session, older_game, _body(game_id="20270009000000"))

    assert _official(session, older_game) == []
    assert _player_on(session, older_game, Side.HOME, 69).league_id is None


def test_a_game_with_no_league_id_cant_be_refreshed(session):
    game = Game(home_team=Team(name="A"), away_team=Team(name="B"))
    session.add(game)
    session.commit()

    with pytest.raises(ValueError, match="league"):
        _refresh(session, game)
