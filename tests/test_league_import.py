from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from sihf_fixtures import (
    GAME_LINK,
    FakeLeagueSource,
    Lineup,
    sihf_game_overview,
)
from sqlalchemy import func, select

from hockey_analyzer.domain.enums import Position, RinkType, Side, UnitType
from hockey_analyzer.domain.game_setup import GameSetupService, SameTeamBothSidesError
from hockey_analyzer.domain.models import (
    Game,
    GameRosterEntry,
    GameUnitAssignment,
    Player,
    Team,
)
from hockey_analyzer.league_import import (
    AlreadyImportedError,
    ImportResolution,
    InvalidGameLinkError,
    LeagueImportService,
    LeagueSourceError,
    ManualEntryFallbackError,
    PlayerMatchStatus,
    PlayerRosteredTwiceError,
    TeamMatchStatus,
)


@pytest.fixture
def source():
    return FakeLeagueSource()


@pytest.fixture
def service(session, source):
    return LeagueImportService(session, source)


# -- game link ------------------------------------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://www.sihf.ch/de/game-center/game/20270009263101",
        "https://www.sihf.ch/fr/game-center/game/20270009263101/",
        "https://www.sihf.ch/de/game-center/game/aufstellung/20270009263101",
        "https://www.sihf.ch/de/game-center/game/20270009263101?tab=summary#top",
        "sihf.ch/de/game-center/game/20270009263101",
        "  20270009263101 ",
    ],
)
def test_game_id_is_read_from_any_form_of_game_link(service, source, link):
    service.propose(link)

    assert set(source.requested_game_ids) == {"20270009263101"}


@pytest.mark.parametrize(
    "link",
    [
        "",
        "https://www.sihf.ch/de/game-center/team/10-4-103010",
        "https://example.com/de/game-center/game/20270009263101",
        "not a link",
    ],
)
def test_link_that_isnt_a_league_game_is_rejected_without_fetching(
    service, source, link
):
    with pytest.raises(InvalidGameLinkError):
        service.propose(link)

    assert source.requested_game_ids == []


# -- game metadata ----------------------------------------------------------


def test_game_metadata_and_score_come_from_the_league_api(service):
    proposal = service.propose(GAME_LINK)

    assert proposal.league_id == "20270009263101"
    assert proposal.date == date(2026, 9, 5)
    assert proposal.venue == "Centre Sportif de la Patinoire"
    assert (proposal.home_score, proposal.away_score) == (7, 1)
    # Same shape as Game.period_scores.
    assert proposal.period_scores == [
        {"home": 4, "away": 1},
        {"home": 3, "away": 0},
        {"home": 0, "away": 0},
    ]


def test_already_imported_game_is_pointed_at(service, session):
    existing = Game(league_id="20270009263101")
    session.add(existing)
    session.flush()

    assert service.propose(GAME_LINK).existing_game_id == existing.id


def test_game_not_yet_imported_has_no_existing_game(service, session):
    session.add(Game(league_id="20270009000000"))
    session.flush()

    assert service.propose(GAME_LINK).existing_game_id is None


# -- team resolution --------------------------------------------------------


def test_team_with_matching_league_id_is_linked_silently(service, session):
    # A local rename since the last import doesn't matter: league_id wins.
    existing = Team(name="Château", league_id="103010")
    session.add(existing)
    session.flush()

    proposal = service.propose(GAME_LINK)

    home = proposal.team(Side.HOME)
    assert home.status == TeamMatchStatus.LINKED
    assert home.team_id == existing.id
    assert home.league_id == "103010"
    assert home.name == "HC Château-d'Oex"


def test_team_matching_only_by_name_needs_confirmation(service, session):
    existing = Team(name="hc chateau-d'oex")
    session.add(existing)
    session.flush()

    proposal = service.propose(GAME_LINK)

    home = proposal.team(Side.HOME)
    assert home.status == TeamMatchStatus.NEEDS_CONFIRMATION
    # Not linked until the user confirms -- only offered as the candidate.
    assert home.team_id is None
    assert [c.team_id for c in home.candidates] == [existing.id]
    assert home.candidates[0].name == "hc chateau-d'oex"


def test_unmatched_team_is_proposed_as_new_from_scraped_name_and_league_id(
    service,
):
    proposal = service.propose(GAME_LINK)

    away = proposal.team(Side.AWAY)
    assert away.status == TeamMatchStatus.NEW
    assert away.team_id is None
    assert away.candidates == ()
    assert away.name == "HC Monthey"
    assert away.league_id == "104254"


def test_same_named_team_with_a_different_league_id_is_not_a_candidate(
    service, session
):
    # Provably a different league team (e.g. another division's side).
    session.add(Team(name="HC Monthey", league_id="999999"))
    session.flush()

    away = service.propose(GAME_LINK).team(Side.AWAY)

    assert away.status == TeamMatchStatus.NEW
    assert away.candidates == ()


def test_user_team_flag_is_carried_for_prefilling_never_set_on_new_teams(
    service, session
):
    mine = Team(name="Château", league_id="103010", is_user_team=True)
    also_mine = Team(name="HC Monthey", is_user_team=True)
    session.add_all([mine, also_mine])
    session.flush()

    proposal = service.propose(GAME_LINK)

    assert proposal.team(Side.HOME).is_user_team is True
    # Unconfirmed name match: the flag rides on the candidate, not the side.
    away = proposal.team(Side.AWAY)
    assert away.is_user_team is False
    assert away.candidates[0].is_user_team is True


def test_new_team_is_never_proposed_as_the_user_team(service):
    proposal = service.propose(GAME_LINK)

    assert proposal.team(Side.HOME).is_user_team is False
    assert proposal.team(Side.AWAY).is_user_team is False


def test_team_league_id_is_the_sihf_team_id_as_a_string(service):
    proposal = service.propose(GAME_LINK)

    assert proposal.team(Side.HOME).league_id == "103010"
    assert proposal.team(Side.AWAY).league_id == "104254"


# -- roster rows ---------------------------------------------------------------


def test_roster_rows_carry_jersey_name_and_game_of_the_day_position(service):
    proposal = service.propose(GAME_LINK)

    away = [
        (row.jersey_number, row.full_name, row.position)
        for row in proposal.roster_for(Side.AWAY)
    ]
    # The lineup's six position groups map onto the full Position enum,
    # listed in the API's order.
    assert away == [
        (30, "Schmid Björn", Position.GOALIE),
        (5, "Roth Arnaud", Position.DEFENSE),
        (26, "Eriksson Nilsson Karl Ludvig", Position.DEFENSE),
        (15, "Brogli Gaël", Position.LEFT_WING),
        (21, "Galley Noham", Position.CENTER),
        (18, "Putallaz Romain", Position.RIGHT_WING),
    ]


def test_players_listed_outside_a_position_group_have_no_game_position(session):
    lineup = Lineup(goalies=[(1, "Tor Hüter")], others=[(99, "Extra Erich")])
    body = sihf_game_overview(home_lineup=lineup)
    service = LeagueImportService(session, FakeLeagueSource(body))

    rows = service.propose(GAME_LINK).roster_for(Side.HOME)

    assert [(row.jersey_number, row.position) for row in rows] == [
        (1, Position.GOALIE),
        (99, None),
    ]


def test_roster_rows_are_split_by_team_side(service):
    proposal = service.propose(GAME_LINK)

    home_jerseys = {row.jersey_number for row in proposal.roster_for(Side.HOME)}
    assert home_jerseys == {39, 41, 71, 25, 22, 13, 69, 28, 6, 55, 11, 23}
    assert all(row.side == Side.HOME for row in proposal.roster_for(Side.HOME))


def test_head_coach_and_absent_players_are_not_roster_rows(session):
    lineup = Lineup(
        goalies=[(1, "Tor Hüter")],
        centers=[(9, "Mitte Mann")],
        head_coach="Trainer Tom",
        absences=[(17, "Verletzt Victor")],
    )
    body = sihf_game_overview(home_lineup=lineup)
    service = LeagueImportService(session, FakeLeagueSource(body))

    rows = service.propose(GAME_LINK).roster_for(Side.HOME)

    assert [(row.jersey_number, row.full_name) for row in rows] == [
        (1, "Tor Hüter"),
        (9, "Mitte Mann"),
    ]


def test_unmatched_player_is_proposed_as_new(service):
    row = service.propose(GAME_LINK).roster_for(Side.AWAY)[0]

    assert row.status == PlayerMatchStatus.NEW
    assert row.player_id is None
    assert row.candidates == ()


# -- player resolution -----------------------------------------------------------


def _row(proposal, full_name):
    return next(row for row in proposal.roster if row.full_name == full_name)


def test_exact_player_name_match_is_linked_silently(service, session):
    # Case- and accent-insensitive, but otherwise exact.
    existing = Player(full_name="memeteau MATTHIAS")
    session.add(existing)
    session.flush()

    row = _row(service.propose(GAME_LINK), "Mémeteau Matthias")

    assert row.status == PlayerMatchStatus.LINKED
    assert row.player_id == existing.id


def test_near_miss_player_name_defaults_to_new_with_candidates(service, session):
    reordered = Player(full_name="Matthias Mémeteau")
    misspelled = Player(full_name="Memeteau Mathias")
    unrelated = Player(full_name="Someone Else")
    session.add_all([reordered, misspelled, unrelated])
    session.flush()

    row = _row(service.propose(GAME_LINK), "Mémeteau Matthias")

    assert row.status == PlayerMatchStatus.NEW
    assert row.player_id is None
    assert {c.player_id for c in row.candidates} == {reordered.id, misspelled.id}
    assert {c.full_name for c in row.candidates} == {
        "Matthias Mémeteau",
        "Memeteau Mathias",
    }


def test_several_exact_player_name_matches_default_to_new(service, session):
    namesakes = [Player(full_name="Galley Noham"), Player(full_name="Galley Noham")]
    session.add_all(namesakes)
    session.flush()

    row = _row(service.propose(GAME_LINK), "Galley Noham")

    assert row.status == PlayerMatchStatus.NEW
    assert [c.player_id for c in row.candidates] == [p.id for p in namesakes]


def test_game_position_is_proposed_for_the_roster_entry_only(service, session):
    # Nominally a center; lined up on the left wing in this game.
    existing = Player(full_name="König Tim", position=Position.CENTER)
    session.add(existing)
    session.commit()

    row = _row(service.propose(GAME_LINK), "König Tim")

    assert (row.player_id, row.position) == (existing.id, Position.LEFT_WING)
    assert session.get(Player, existing.id).position == Position.CENTER


def _licence(full_name):
    """The licence number the fixture body gives the player listed as
    `full_name`, as `Player.league_id` stores it."""
    players = sihf_game_overview()["players"]
    return str(next(p["id"] for p in players if p["fullName"] == full_name))


def test_roster_rows_carry_the_players_licence_number(service):
    row = _row(service.propose(GAME_LINK), "König Tim")

    assert row.league_id == _licence("König Tim")


def test_player_with_matching_licence_number_is_linked_silently(service, session):
    # Spelled differently locally: the licence number decides, not the name.
    existing = Player(full_name="Tim Koenig", league_id=_licence("König Tim"))
    session.add(existing)
    session.flush()

    row = _row(service.propose(GAME_LINK), "König Tim")

    assert row.status == PlayerMatchStatus.LINKED
    assert row.player_id == existing.id


def test_licence_number_match_wins_over_an_exact_name_match(service, session):
    namesake = Player(full_name="König Tim")
    holder = Player(full_name="T. König", league_id=_licence("König Tim"))
    session.add_all([namesake, holder])
    session.flush()

    row = _row(service.propose(GAME_LINK), "König Tim")

    assert (row.status, row.player_id) == (PlayerMatchStatus.LINKED, holder.id)


def test_a_player_holding_another_licence_number_is_no_name_match(service, session):
    # Same name, different licence number: provably someone else.
    session.add(Player(full_name="König Tim", league_id="999999"))
    session.flush()

    row = _row(service.propose(GAME_LINK), "König Tim")

    assert row.status == PlayerMatchStatus.NEW
    assert row.candidates == ()


# -- staged, never written -------------------------------------------------------


def test_proposing_writes_nothing(service, session):
    session.add_all(
        [
            Team(name="HC Monthey"),
            Team(name="Château", league_id="103010"),
            Player(full_name="Galley Noham"),
            Player(full_name="Memeteau Mathias"),
        ]
    )
    session.commit()
    counts_before = _row_counts(session)

    service.propose(GAME_LINK)

    assert not (session.new or session.dirty or session.deleted)
    assert _row_counts(session) == counts_before


def _row_counts(session):
    return {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (Game, Team, Player, GameRosterEntry, GameUnitAssignment)
    }


# -- unusable league data: fall back to manual entry ---------------------------


@pytest.mark.parametrize(
    "body",
    [
        LeagueSourceError("HTTP 500"),
        {"error": "GameNotFound"},
        # A game not played yet: no result, lineups not out.
        sihf_game_overview(
            home_score=None,
            away_score=None,
            home_lineup=Lineup(),
            away_lineup=Lineup(),
        ),
        sihf_game_overview(home_lineup=Lineup(), away_lineup=Lineup()),
        sihf_game_overview(game_id="20270009999999"),
    ],
    ids=[
        "fetch fails",
        "not a game body",
        "game not played yet",
        "no lineups",
        "body of a different game",
    ],
)
def test_unusable_league_data_signals_fallback_to_manual_entry(session, body):
    service = LeagueImportService(session, FakeLeagueSource(body))

    with pytest.raises(ManualEntryFallbackError):
        service.propose(GAME_LINK)


# -- units prefilled from the lineup ---------------------------------------------


def _uneven_lineup():
    """4 LW / 5 C / 4 RW and 4 LD / 3 RD, plus two goalies and an extra."""
    return Lineup(
        goalies=[(1, "Tor Erster"), (30, "Tor Zweiter")],
        defense_left=[(2, "Links Eins"), (4, "Links Zwei"), (6, "Links Drei")]
        + [(8, "Links Vier")],
        defense_right=[(3, "Rechts Eins"), (5, "Rechts Zwei"), (7, "Rechts Drei")],
        left_wings=[(10 + n, f"Flügel Links{n}") for n in range(1, 5)],
        centers=[(20 + n, f"Mitte Center{n}") for n in range(1, 6)],
        right_wings=[(30 + n, f"Flügel Rechts{n}") for n in range(1, 5)],
        others=[(99, "Extra Erich")],
    )


def _uneven_service(session):
    body = sihf_game_overview(home_lineup=_uneven_lineup())
    return LeagueImportService(session, FakeLeagueSource(body))


def test_the_nth_player_of_each_side_makes_up_line_n(session):
    rows = _uneven_service(session).propose(GAME_LINK).roster_for(Side.HOME)

    units = {row.jersey_number: row.unit for row in rows}
    for n in range(1, 5):
        line = {units[10 + n], units[20 + n], units[30 + n]}
        assert line == {(UnitType.FORWARD_LINE, n)}
    assert {units[2], units[3]} == {(UnitType.DEFENSE_PAIR, 1)}
    assert {units[6], units[7]} == {(UnitType.DEFENSE_PAIR, 3)}


def test_only_as_many_units_as_the_shortest_side_are_prefilled(session):
    rows = _uneven_service(session).propose(GAME_LINK).roster_for(Side.HOME)

    by_jersey = {row.jersey_number: row for row in rows}
    # A 5th center and a 4th left defenseman are rostered, with no unit.
    assert by_jersey[25].position == Position.CENTER
    assert by_jersey[25].unit is None
    assert by_jersey[8].position == Position.DEFENSE
    assert by_jersey[8].unit is None
    lines = {row.unit for row in rows if row.unit is not None}
    assert lines == {(UnitType.FORWARD_LINE, n) for n in range(1, 5)} | {
        (UnitType.DEFENSE_PAIR, n) for n in range(1, 4)
    }


def test_goalies_and_extras_get_no_unit(session):
    rows = _uneven_service(session).propose(GAME_LINK).roster_for(Side.HOME)

    by_jersey = {row.jersey_number: row for row in rows}
    assert [by_jersey[j].unit for j in (1, 30, 99)] == [None, None, None]
    assert by_jersey[99].position is None


def test_a_side_missing_a_forward_group_gets_no_forward_lines(session):
    lineup = Lineup(
        goalies=[(1, "Tor Hüter")],
        left_wings=[(10, "Links Lars")],
        centers=[(20, "Mitte Max")],
    )
    service = LeagueImportService(
        session, FakeLeagueSource(sihf_game_overview(home_lineup=lineup))
    )

    rows = service.propose(GAME_LINK).roster_for(Side.HOME)

    assert [row.unit for row in rows] == [None, None, None]


def test_confirming_writes_the_prefilled_units(session):
    service = _uneven_service(session)
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal))

    jerseys = {
        (entry.team_id, entry.player_id): entry.jersey_number
        for entry in game.roster_entries
    }
    written = {
        (jerseys[(unit.team_id, unit.player_id)], unit.unit_type, unit.unit_number)
        for unit in session.scalars(
            select(GameUnitAssignment).where(GameUnitAssignment.game_id == game.id)
        )
        if unit.team_id == game.home_team_id
    }
    expected = {
        (row.jersey_number, *row.unit)
        for row in proposal.roster_for(Side.HOME)
        if row.unit is not None
    }
    assert written == expected
    assert len(written) == 4 * 3 + 3 * 2
    # The sample away lineup has one player per group: line 1, pair 1.
    away_units = session.scalar(
        select(func.count())
        .select_from(GameUnitAssignment)
        .where(GameUnitAssignment.team_id == game.away_team_id)
    )
    assert away_units == 3 + 2


def test_no_goalie_power_play_or_penalty_kill_unit_is_written(session):
    service = _uneven_service(session)
    proposal = service.propose(GAME_LINK)

    service.confirm(proposal, _as_proposed(proposal))

    unit_types = set(session.scalars(select(GameUnitAssignment.unit_type)))
    assert unit_types == {UnitType.FORWARD_LINE, UnitType.DEFENSE_PAIR}


def test_a_relinked_rows_units_follow_it(service, session):
    existing = Player(full_name="Someone Else")
    session.add(existing)
    session.commit()
    proposal = service.propose(GAME_LINK)
    player_ids = list(ImportResolution.as_proposed(proposal).player_ids)
    row = _row(proposal, "Graf Marcel")
    player_ids[proposal.roster.index(row)] = existing.id

    game = service.confirm(
        proposal, _as_proposed(proposal, player_ids=tuple(player_ids))
    )

    unit = session.scalar(
        select(GameUnitAssignment).where(GameUnitAssignment.player_id == existing.id)
    )
    assert (unit.game_id, unit.team_id) == (game.id, game.home_team_id)
    assert (unit.unit_type, unit.unit_number) == row.unit == (UnitType.FORWARD_LINE, 2)


def test_imported_units_are_editable_like_hand_made_ones(service, session):
    proposal = service.propose(GAME_LINK)
    game = service.confirm(proposal, _as_proposed(proposal))
    graf = next(
        entry.player_id
        for entry in game.roster_entries
        if entry.player.full_name == "Graf Marcel"
    )
    setup = GameSetupService(session)
    assert setup.player_unit_assignments(game_id=game.id, player_id=graf) == {
        UnitType.FORWARD_LINE: 2
    }

    setup.assign_unit(
        game_id=game.id,
        player_id=graf,
        team_id=game.home_team_id,
        unit_type=UnitType.FORWARD_LINE,
        unit_number=1,
    )

    assert setup.player_unit_assignments(game_id=game.id, player_id=graf) == {
        UnitType.FORWARD_LINE: 1
    }


# -- confirming a reviewed proposal ---------------------------------------------


def _as_proposed(proposal, **overrides):
    """The resolution a review screen submits when the user changes
    nothing but `overrides`."""
    return replace(ImportResolution.as_proposed(proposal), **overrides)


def test_confirming_creates_the_game_with_its_league_metadata(service, session):
    proposal = service.propose(GAME_LINK)

    game = service.confirm(
        proposal, _as_proposed(proposal, user_team=Side.HOME, rink_type=RinkType.NHL)
    )

    stored = session.get(Game, game.id)
    assert stored.league_id == "20270009263101"
    assert stored.date == date(2026, 9, 5)
    assert stored.venue == "Centre Sportif de la Patinoire"
    assert (stored.home_score, stored.away_score) == (7, 1)
    assert stored.period_scores == proposal.period_scores
    assert stored.rink_type == RinkType.NHL


def test_confirming_creates_new_teams_from_the_scraped_names_and_league_ids(
    service, session
):
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal, user_team=Side.AWAY))

    home, away = game.home_team, game.away_team
    assert (home.name, home.league_id) == ("HC Château-d'Oex", "103010")
    assert (away.name, away.league_id) == ("HC Monthey", "104254")
    assert (home.is_user_team, away.is_user_team) == (False, True)


def test_confirming_writes_every_roster_row_as_shown(service, session):
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal))

    written = {
        (entry.team_id, entry.jersey_number, entry.player.full_name, entry.position)
        for entry in game.roster_entries
    }
    expected = {
        (
            game.home_team_id if row.side == Side.HOME else game.away_team_id,
            row.jersey_number,
            row.full_name,
            row.position,
        )
        for row in proposal.roster
    }
    assert written == expected
    assert len(game.roster_entries) == len(proposal.roster)


def test_confirming_links_a_league_id_matched_team_instead_of_creating_one(
    service, session
):
    existing = Team(name="Château", league_id="103010")
    session.add(existing)
    session.commit()
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal))

    assert game.home_team_id == existing.id
    assert session.scalar(select(func.count()).select_from(Team)) == 2


def test_a_confirmed_name_match_learns_the_teams_league_id(service, session):
    existing = Team(name="HC Monthey")
    session.add(existing)
    session.commit()
    proposal = service.propose(GAME_LINK)
    assert proposal.away.status == TeamMatchStatus.NEEDS_CONFIRMATION

    game = service.confirm(proposal, _as_proposed(proposal, away_team_id=existing.id))

    assert game.away_team_id == existing.id
    # So the next import of this team links silently.
    assert session.get(Team, existing.id).league_id == "104254"
    assert service.propose(GAME_LINK).away.status == TeamMatchStatus.LINKED


def test_an_unconfirmed_name_match_creates_a_new_team(service, session):
    existing = Team(name="HC Monthey")
    session.add(existing)
    session.commit()
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal))

    assert game.away_team_id != existing.id
    assert session.get(Team, existing.id).league_id is None


def test_user_team_prefill_comes_only_from_a_linked_flagged_team(service, session):
    session.add(Team(name="Château", league_id="103010", is_user_team=True))
    session.commit()

    resolution = ImportResolution.as_proposed(service.propose(GAME_LINK))

    assert resolution.user_team == Side.HOME


def test_user_team_is_not_prefilled_when_nothing_is_known(service):
    resolution = ImportResolution.as_proposed(service.propose(GAME_LINK))

    assert resolution.user_team is None


def test_user_team_is_not_prefilled_when_both_linked_teams_are_flagged(
    service, session
):
    session.add_all(
        [
            Team(name="A", league_id="103010", is_user_team=True),
            Team(name="B", league_id="104254", is_user_team=True),
        ]
    )
    session.commit()

    resolution = ImportResolution.as_proposed(service.propose(GAME_LINK))

    assert resolution.user_team is None


def test_the_user_team_answer_flags_the_chosen_team(service, session):
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal, user_team=Side.AWAY))

    assert game.away_team.is_user_team is True


@pytest.mark.parametrize("answer", [Side.AWAY, None], ids=["other side", "neither"])
def test_the_user_team_answer_never_unflags_a_team(service, session, answer):
    # Several teams may be the user's (CONTEXT.md's Team): a per-game
    # answer isn't a reason to take the flag off one.
    mine = Team(name="Château", league_id="103010", is_user_team=True)
    session.add(mine)
    session.commit()
    proposal = service.propose(GAME_LINK)

    service.confirm(proposal, _as_proposed(proposal, user_team=answer))

    assert session.get(Team, mine.id).is_user_team is True


def test_answering_neither_team_flags_no_new_team(service, session):
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal, user_team=None))

    assert (game.home_team.is_user_team, game.away_team.is_user_team) == (
        False,
        False,
    )


def test_confirming_links_matched_players_and_creates_the_rest(service, session):
    exact = Player(full_name="Galley Noham")
    near_miss = Player(full_name="Memeteau Mathias")
    session.add_all([exact, near_miss])
    session.commit()
    proposal = service.propose(GAME_LINK)
    player_ids = list(ImportResolution.as_proposed(proposal).player_ids)
    # The user overrides the near-miss row's default "new player".
    near_miss_row = proposal.roster.index(_row(proposal, "Mémeteau Matthias"))
    player_ids[near_miss_row] = near_miss.id

    game = service.confirm(
        proposal, _as_proposed(proposal, player_ids=tuple(player_ids))
    )

    by_jersey = {
        (entry.team_id, entry.jersey_number): entry.player_id
        for entry in game.roster_entries
    }
    assert by_jersey[(game.away_team_id, 21)] == exact.id
    assert by_jersey[(game.home_team_id, 69)] == near_miss.id
    players = session.scalar(select(func.count()).select_from(Player))
    assert players == 2 + len(proposal.roster) - 2


def test_new_players_never_get_the_game_position_as_their_own(service, session):
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal))

    assert all(entry.player.position is None for entry in game.roster_entries)
    assert all(entry.position is not None for entry in game.roster_entries)


def test_new_players_carry_their_licence_number(service, session):
    proposal = service.propose(GAME_LINK)

    game = service.confirm(proposal, _as_proposed(proposal))

    assert {
        (entry.player.full_name, entry.player.league_id)
        for entry in game.roster_entries
    } == {(row.full_name, _licence(row.full_name)) for row in proposal.roster}


def _linking(proposal, overrides):
    """The as-proposed player choices, with `overrides` (full name ->
    player id, or None for a new player) applied."""
    player_ids = list(ImportResolution.as_proposed(proposal).player_ids)
    for full_name, player_id in overrides.items():
        player_ids[proposal.roster.index(_row(proposal, full_name))] = player_id
    return _as_proposed(proposal, player_ids=tuple(player_ids))


def test_a_confirmed_name_match_learns_the_players_licence_number(service, session):
    exact = Player(full_name="Galley Noham")
    near_miss = Player(full_name="Memeteau Mathias")
    session.add_all([exact, near_miss])
    session.commit()
    proposal = service.propose(GAME_LINK)

    service.confirm(proposal, _linking(proposal, {"Mémeteau Matthias": near_miss.id}))

    assert session.get(Player, exact.id).league_id == _licence("Galley Noham")
    assert session.get(Player, near_miss.id).league_id == _licence("Mémeteau Matthias")


def test_an_unconfirmed_name_match_does_not_learn_the_licence_number(service, session):
    near_miss = Player(full_name="Memeteau Mathias")
    session.add(near_miss)
    session.commit()
    proposal = service.propose(GAME_LINK)
    assert _row(proposal, "Mémeteau Matthias").candidates

    game = service.confirm(proposal, _as_proposed(proposal))

    assert session.get(Player, near_miss.id).league_id is None
    entry = next(e for e in game.roster_entries if e.jersey_number == 69)
    assert entry.player_id != near_miss.id
    assert entry.player.league_id == _licence("Mémeteau Matthias")


@pytest.mark.parametrize("override", ["new player", "another player"])
def test_a_held_licence_number_is_never_given_to_a_second_player(
    service, session, override
):
    licence = _licence("König Tim")
    holder = Player(full_name="Tim Koenig", league_id=licence)
    other = Player(full_name="Someone Else")
    session.add_all([holder, other])
    session.commit()
    proposal = service.propose(GAME_LINK)
    # The user overrides the licence-number link on the review screen.
    choice = None if override == "new player" else other.id

    game = service.confirm(proposal, _linking(proposal, {"König Tim": choice}))

    entry = next(e for e in game.roster_entries if e.jersey_number == 55)
    assert entry.player_id == holder.id
    holders = session.scalars(select(Player.id).where(Player.league_id == licence))
    assert holders.all() == [holder.id]
    assert session.get(Player, other.id).league_id is None


def test_linking_a_row_to_a_player_with_another_licence_number_is_refused(
    service, session
):
    other = Player(full_name="Someone Else", league_id="999999")
    session.add(other)
    session.commit()
    proposal = service.propose(GAME_LINK)
    counts_before = _row_counts(session)

    with pytest.raises(ValueError):
        service.confirm(proposal, _linking(proposal, {"König Tim": other.id}))

    assert _row_counts(session) == counts_before
    assert session.get(Player, other.id).league_id == "999999"


def test_confirming_an_already_imported_game_is_refused(service, session):
    proposal = service.propose(GAME_LINK)
    first = service.confirm(proposal, _as_proposed(proposal))
    counts_before = _row_counts(session)

    with pytest.raises(AlreadyImportedError) as refused:
        service.confirm(proposal, _as_proposed(proposal))

    assert refused.value.existing_game_id == first.id
    assert _row_counts(session) == counts_before


def test_linking_both_sides_to_one_team_is_refused(service, session):
    team = Team(name="HC Monthey")
    session.add(team)
    session.commit()
    proposal = service.propose(GAME_LINK)
    counts_before = _row_counts(session)

    with pytest.raises(SameTeamBothSidesError):
        service.confirm(
            proposal,
            _as_proposed(proposal, home_team_id=team.id, away_team_id=team.id),
        )

    assert _row_counts(session) == counts_before


def test_linking_one_player_to_two_rows_is_refused(service, session):
    player = Player(full_name="Someone")
    session.add(player)
    session.commit()
    proposal = service.propose(GAME_LINK)
    player_ids = (player.id, player.id) + (None,) * (len(proposal.roster) - 2)
    counts_before = _row_counts(session)

    with pytest.raises(PlayerRosteredTwiceError):
        service.confirm(proposal, _as_proposed(proposal, player_ids=player_ids))

    assert _row_counts(session) == counts_before


def test_a_failure_partway_through_writes_nothing(service, session):
    existing = Team(name="HC Monthey")
    session.add(existing)
    session.commit()
    proposal = service.propose(GAME_LINK)
    # The last row links a player that doesn't exist -- found only after
    # the teams, game and earlier rows were already staged.
    player_ids = (None,) * (len(proposal.roster) - 1) + (9999,)
    counts_before = _row_counts(session)

    with pytest.raises(KeyError):
        service.confirm(
            proposal,
            _as_proposed(proposal, away_team_id=existing.id, player_ids=player_ids),
        )

    assert _row_counts(session) == counts_before
    assert session.get(Team, existing.id).league_id is None


def test_creating_a_second_team_with_a_known_league_id_is_refused(service, session):
    session.add(Team(name="Château", league_id="103010"))
    session.commit()
    proposal = service.propose(GAME_LINK)
    counts_before = _row_counts(session)

    with pytest.raises(ValueError):
        service.confirm(proposal, _as_proposed(proposal, home_team_id=None))

    assert _row_counts(session) == counts_before


# -- review-screen override lists -------------------------------------------------


def test_linkable_teams_leave_out_other_league_teams(service, session):
    unlinked = Team(name="B Unlinked")
    same = Team(name="A Same", league_id="104254")
    session.add_all([unlinked, same, Team(name="C Other", league_id="1")])
    session.commit()

    linkable = service.linkable_teams("104254")

    assert [team.team_id for team in linkable] == [same.id, unlinked.id]


def test_known_players_lists_everyone_by_name(service, session):
    bob, amy = Player(full_name="Bob"), Player(full_name="Amy")
    session.add_all([bob, amy])
    session.commit()

    assert [p.player_id for p in service.known_players()] == [amy.id, bob.id]
