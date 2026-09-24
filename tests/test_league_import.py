from __future__ import annotations

from datetime import date

import pytest
from sihf_fixtures import (
    GAME_LINK,
    FakeLeagueSource,
    Lineup,
    pdf_document,
    sihf_game_pdf,
)
from sqlalchemy import func, select

from hockey_analyzer.domain.enums import Position, Side
from hockey_analyzer.domain.models import Game, GameRosterEntry, Player, Team
from hockey_analyzer.league_import import (
    InvalidGameLinkError,
    LeagueImportService,
    LeagueSourceError,
    ManualEntryFallbackError,
    PlayerMatchStatus,
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


def test_game_metadata_and_score_come_from_the_pdf(service):
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
    existing = Team(name="Château", league_id="10-4-103010")
    session.add(existing)
    session.flush()

    proposal = service.propose(GAME_LINK)

    home = proposal.team(Side.HOME)
    assert home.status == TeamMatchStatus.LINKED
    assert home.team_id == existing.id
    assert home.league_id == "10-4-103010"
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
    assert away.league_id == "10-4-104254"


def test_same_named_team_with_a_different_league_id_is_not_a_candidate(
    service, session
):
    # Provably a different league team (e.g. another division's side).
    session.add(Team(name="HC Monthey", league_id="10-9-999999"))
    session.flush()

    away = service.propose(GAME_LINK).team(Side.AWAY)

    assert away.status == TeamMatchStatus.NEW
    assert away.candidates == ()


def test_user_team_flag_is_carried_for_prefilling_never_set_on_new_teams(
    service, session
):
    mine = Team(name="Château", league_id="10-4-103010", is_user_team=True)
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


@pytest.mark.parametrize(
    "page",
    [
        LeagueSourceError("connection reset"),
        "<html><body>redesigned page, no header links</body></html>",
    ],
    ids=["fetch fails", "page doesn't parse"],
)
def test_lost_league_id_source_degrades_team_matching_to_name_only(session, page):
    # Would be a silent league_id link if the game page were readable.
    existing = Team(name="HC Monthey", league_id="10-4-104254")
    session.add(existing)
    session.flush()
    service = LeagueImportService(session, FakeLeagueSource(page=page))

    proposal = service.propose(GAME_LINK)

    assert proposal.team_league_ids_found is False
    away = proposal.team(Side.AWAY)
    assert away.league_id is None
    assert away.status == TeamMatchStatus.NEEDS_CONFIRMATION
    assert [c.team_id for c in away.candidates] == [existing.id]
    # The PDF parse survives: the other side is still proposed from it.
    home = proposal.team(Side.HOME)
    assert (home.name, home.status) == ("HC Château-d'Oex", TeamMatchStatus.NEW)


def test_proposal_reports_when_team_league_ids_were_found(service):
    assert service.propose(GAME_LINK).team_league_ids_found is True


# -- roster rows ---------------------------------------------------------------


def test_roster_rows_carry_jersey_name_and_game_of_the_day_position(service):
    proposal = service.propose(GAME_LINK)

    away = [
        (row.jersey_number, row.full_name, row.position)
        for row in proposal.roster_for(Side.AWAY)
    ]
    # Captain/"(1st)"/"(E)" markers are stripped from the name; the six
    # league position-group headers map onto the full Position enum.
    assert away == [
        (30, "Schmid Björn", Position.GOALIE),
        (5, "Roth Arnaud", Position.DEFENSE),
        (26, "Eriksson Nilsson Karl Ludvig", Position.DEFENSE),
        (21, "Galley Noham", Position.CENTER),
        (15, "Brogli Gaël", Position.LEFT_WING),
        (18, "Putallaz Romain", Position.RIGHT_WING),
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
    pdf = sihf_game_pdf(home_lineup=lineup)
    service = LeagueImportService(session, FakeLeagueSource(pdf=pdf))

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


# -- staged, never written -------------------------------------------------------


def test_proposing_writes_nothing(service, session):
    session.add_all(
        [
            Team(name="HC Monthey"),
            Team(name="Château", league_id="10-4-103010"),
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
        for model in (Game, Team, Player, GameRosterEntry)
    }


# -- PDF failure: fall back to manual entry ------------------------------------


@pytest.mark.parametrize(
    "pdf",
    [
        LeagueSourceError("HTTP 400"),
        b"<html>not a pdf at all</html>",
        pdf_document([[(20.0, 800.0, "Some other document")]]),
        # The pre-game export: a structurally empty template.
        sihf_game_pdf(
            home_score=None,
            away_score=None,
            home_lineup=Lineup(),
            away_lineup=Lineup(),
        ),
        sihf_game_pdf(game_id="20270009999999"),
    ],
    ids=[
        "fetch fails",
        "not a PDF",
        "not the game export",
        "game not played yet",
        "export of a different game",
    ],
)
def test_pdf_failure_signals_fallback_to_manual_entry(session, pdf):
    service = LeagueImportService(session, FakeLeagueSource(pdf=pdf))

    with pytest.raises(ManualEntryFallbackError):
        service.propose(GAME_LINK)
