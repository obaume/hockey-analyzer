"""The SIHF league as an import source: turning a `sihf.ch` game link into a
game ID, fetching that game's `gameoverview` body from the SIHF statistics
API, and reading it through sportsdataverse's parsers (ADR-0013) into this
app's own plain dataclasses -- the polars frames those parsers return never
leave this module.

The API isn't a contract either, so every parse failure here raises a
`LeagueSourceError` rather than returning partial data -- the caller decides
how far a failed source degrades the import.

`sportsdataverse` is imported inside the functions that need it, not at
module level: importing any part of it imports the whole package (every
league, pandas, polars and its ML stack -- xgboost, scikit-learn, scipy),
which takes seconds, so it's paid on the first league import rather than
at app start.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_
from typing import Any

from hockey_analyzer.domain.enums import Position, Side


class LeagueSourceError(Exception):
    """A league game couldn't be fetched or its body didn't parse."""


class InvalidGameLinkError(ValueError):
    """The text given as a game link isn't a league game link (or ID)."""


# `https://www.sihf.ch/{lang}/game-center/game/[{tab}/]{game_id}` -- any
# language, any per-game tab (e.g. `aufstellung`), scheme optional. The
# link's ID is the API's game ID.
_GAME_LINK = re.compile(
    r"^(?:https?://)?(?:www\.)?sihf\.ch/[a-z]{2}/game-center/game/"
    r"(?:[\w-]+/)?(\d+)/?(?:[?#].*)?$"
)
_BARE_GAME_ID = re.compile(r"^\d+$")


def parse_game_link(link: str) -> str:
    """The league game ID a pasted game link (or bare ID) points at."""
    link = link.strip()
    if _BARE_GAME_ID.match(link):
        return link
    match = _GAME_LINK.match(link)
    if match is None:
        raise InvalidGameLinkError(f"not a league game link: {link!r}")
    return match.group(1)


@dataclass(frozen=True)
class LeaguePlayer:
    jersey_number: int
    # As the API prints it: "Last First".
    full_name: str
    # The position group the player lined up in for this game; None for a
    # player listed outside the six groups.
    position: Position | None


@dataclass(frozen=True)
class LeagueTeam:
    name: str
    # The SIHF team ID, as a string -- Team.league_id.
    league_id: str | None
    # Dressed players only, in the API's listing order.
    lineup: tuple[LeaguePlayer, ...]


@dataclass(frozen=True)
class LeagueGame:
    """What the league says about one game, before any matching."""

    league_id: str
    date: date_ | None
    venue: str | None
    home_score: int
    away_score: int
    # One {"home": int, "away": int} per period, like Game.period_scores.
    period_scores: list[dict[str, int]]
    home: LeagueTeam
    away: LeagueTeam

    def team(self, side: Side) -> LeagueTeam:
        return self.home if side == Side.HOME else self.away


# sportsdataverse's lineup units -> the game-of-the-day position. `other`
# (dressed, outside the six groups) has none; absentees have no unit at all.
_UNIT_POSITIONS: dict[str, Position | None] = {
    "goalkeeper": Position.GOALIE,
    "defense_left": Position.DEFENSE,
    "defense_right": Position.DEFENSE,
    "forward_left": Position.LEFT_WING,
    "forward_center": Position.CENTER,
    "forward_right": Position.RIGHT_WING,
    "other": None,
}


def parse_game_overview(body: Mapping[str, Any]) -> LeagueGame:
    from sportsdataverse.hockey.sihf import (
        parse_sihf_game_info,
        parse_sihf_game_lineups,
    )

    info_rows = parse_sihf_game_info(body).to_dicts()
    if len(info_rows) != 1 or info_rows[0]["game_id"] is None:
        raise LeagueSourceError("not a game body")
    info = info_rows[0]
    home_score, away_score = info["home_score"], info["away_score"]
    if home_score is None or away_score is None:
        # An unfinished game isn't importable (ticket 11).
        raise LeagueSourceError("no final score (game not finished?)")
    lineups = parse_sihf_game_lineups(body).to_dicts()
    home, away = (
        _team(info, [row for row in lineups if row["team_side"] == side.value], side)
        for side in (Side.HOME, Side.AWAY)
    )
    return LeagueGame(
        league_id=str(info["game_id"]),
        date=_start_date(info["start_date_time"]),
        venue=info["venue_name"] or None,
        home_score=home_score,
        away_score=away_score,
        period_scores=_period_scores(info),
        home=home,
        away=away,
    )


def _team(info: dict[str, Any], rows: list[dict[str, Any]], side: Side) -> LeagueTeam:
    name = info[f"{side.value}_team_name"]
    if not name:
        raise LeagueSourceError(f"no {side.value} team name")
    team_id = info[f"{side.value}_team_id"]
    lineup = tuple(
        LeaguePlayer(row["player_jersey"], row["player_name"], _UNIT_POSITIONS[unit])
        for row in rows
        # A null unit is an absentee: never dressed, so not a roster row.
        if (unit := row["unit"]) is not None
    )
    if not lineup:
        # Also a game whose lineups aren't out yet.
        raise LeagueSourceError(f"the {side.value} team's lineup has no players")
    if any(p.jersey_number is None or not p.full_name for p in lineup):
        raise LeagueSourceError(f"a {side.value} lineup player has no name or jersey")
    return LeagueTeam(name, None if team_id is None else str(team_id), lineup)


def _start_date(start: str | None) -> date_ | None:
    """The calendar date of the start time, as the league prints it (local
    time, with its UTC offset)."""
    try:
        return date_.fromisoformat(start[:10]) if start else None
    except ValueError:
        return None


def _period_scores(info: dict[str, Any]) -> list[dict[str, int]]:
    home, away = info["home_score_by_period"], info["away_score_by_period"]
    if not home or len(home) != len(away) or None in home or None in away:
        raise LeagueSourceError("no period-by-period score")
    return [{"home": h, "away": a} for h, a in zip(home, away, strict=True)]


class SihfApiSource:
    """The live `LeagueSource`: the keyless SIHF statistics API, through
    sportsdataverse's raw-response fetch (ADR-0013). sportsdataverse
    retries and throttles the request itself, and refuses a body for
    another game than the one asked for."""

    def fetch_game_overview(self, game_id: str) -> dict[str, Any]:
        from sportsdataverse.errors import SportsDataverseError
        from sportsdataverse.hockey.sihf import sihf_pbp

        try:
            return sihf_pbp(game_id=int(game_id), return_parsed=False)
        # SportsDataverseError covers a failed fetch (AssetFetchError) and
        # an unknown game (NoDataError); ValueError a rejected request;
        # OSError anything the HTTP layer lets through.
        except (SportsDataverseError, ValueError, OSError) as error:
            raise LeagueSourceError(
                f"couldn't fetch league game {game_id}: {error}"
            ) from error
