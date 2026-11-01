"""Fixture builders for the SIHF statistics API body `LeagueImportService`
reads (see ADR-0013): the `gameoverview` JSON for one game.

`sihf_game_overview` builds a body in the API's real shape -- the `players`
roster keyed by licence number, `lineUps` listing licence numbers by line
slot, the `details` header and the `result` score -- with fictional names,
so every test runs sportsdataverse's real parsers over it. No network access
is involved anywhere here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# (jersey number, name as the API prints it -- "Last First")
LineupPlayer = tuple[int, str]


@dataclass
class Lineup:
    goalies: list[LineupPlayer] = field(default_factory=list)
    defense_left: list[LineupPlayer] = field(default_factory=list)
    defense_right: list[LineupPlayer] = field(default_factory=list)
    left_wings: list[LineupPlayer] = field(default_factory=list)
    centers: list[LineupPlayer] = field(default_factory=list)
    right_wings: list[LineupPlayer] = field(default_factory=list)
    # Dressed, but listed outside any position group.
    others: list[LineupPlayer] = field(default_factory=list)
    head_coach: str = "Coach Person"
    # Scratched, injured or otherwise absent: never dressed.
    absences: list[LineupPlayer] = field(default_factory=list)


GAME_ID = "20270009263101"
GAME_LINK = f"https://www.sihf.ch/de/game-center/game/{GAME_ID}"
HOME_TEAM_ID = 103010
AWAY_TEAM_ID = 104254


def sample_home_lineup() -> Lineup:
    return Lineup(
        goalies=[(39, "Keller Jonas"), (41, "Meyer Luca")],
        defense_left=[(71, "Brunner Noah"), (25, "Weber Carl-Louis")],
        defense_right=[(22, "Frei Jean-Michel"), (13, "Huber Livio Nicola")],
        left_wings=[(6, "Métraux Dorian"), (55, "König Tim")],
        centers=[(69, "Mémeteau Matthias"), (28, "Graf Marcel")],
        right_wings=[(11, "Würth Ezequiel"), (23, "Baumann Mathis")],
    )


def sample_away_lineup() -> Lineup:
    return Lineup(
        goalies=[(30, "Schmid Björn")],
        defense_left=[(5, "Roth Arnaud")],
        defense_right=[(26, "Eriksson Nilsson Karl Ludvig")],
        left_wings=[(15, "Brogli Gaël")],
        centers=[(21, "Galley Noham")],
        right_wings=[(18, "Putallaz Romain")],
    )


def sihf_game_overview(
    *,
    game_id: str = GAME_ID,
    home_team_id: int = HOME_TEAM_ID,
    away_team_id: int = AWAY_TEAM_ID,
    home_name: str = "HC Château-d'Oex",
    away_name: str = "HC Monthey",
    home_score: int | None = 7,
    away_score: int | None = 1,
    period_scores: list[tuple[int, int]] | None = None,
    start: str = "2026-09-05T15:30:00+02:00",
    venue: str = "Centre Sportif de la Patinoire",
    home_lineup: Lineup | None = None,
    away_lineup: Lineup | None = None,
) -> dict[str, Any]:
    """One game's `gameoverview` body. A `None` score reproduces a game
    that hasn't been played: no result, and (with empty `Lineup`s) no
    lineups either. Licence numbers are assigned in listing order."""
    home_lineup = sample_home_lineup() if home_lineup is None else home_lineup
    away_lineup = sample_away_lineup() if away_lineup is None else away_lineup
    if period_scores is None:
        period_scores = [(4, 1), (3, 0), (0, 0)]
    played = home_score is not None and away_score is not None

    players: list[dict[str, Any]] = []

    def licences(team_id: int, listed: list[LineupPlayer]) -> list[int]:
        numbers = []
        for jersey, name in listed:
            licence = 300001 + len(players)
            players.append(
                {
                    "teamId": team_id,
                    "ageGroup": 2000,
                    "licence": "",
                    "id": licence,
                    "fullName": name,
                    "jerseyNumber": jersey,
                }
            )
            numbers.append(licence)
        return numbers

    def lineup_body(team_id: int, lineup: Lineup) -> dict[str, Any]:
        return {
            "goalkeepers": licences(team_id, lineup.goalies),
            "defenders": {
                "left": licences(team_id, lineup.defense_left),
                "right": licences(team_id, lineup.defense_right),
            },
            "forwarders": {
                "left": licences(team_id, lineup.left_wings),
                "right": licences(team_id, lineup.right_wings),
                "center": licences(team_id, lineup.centers),
            },
            "otherPlayers": licences(team_id, lineup.others),
            "taggedPlayers": [],
            "coach": {
                "id": 0,
                "fullName": lineup.head_coach,
                "jerseyNumber": 0,
                "country": "",
            },
        }

    def absentees(team_id: int, lineup: Lineup) -> list[dict[str, Any]]:
        if not lineup.absences:
            return []
        return [
            {
                "id": 3,
                "name": "Überzählig",
                "acronym": "SU",
                "absentees": licences(team_id, lineup.absences),
            }
        ]

    def team(team_id: int, name: str) -> dict[str, Any]:
        return {
            "id": team_id,
            "name": name,
            "acronym": name[:3].upper(),
            "signed": False,
            "type": "team",
        }

    line_ups = {
        "homeTeam": lineup_body(home_team_id, home_lineup),
        "awayTeam": lineup_body(away_team_id, away_lineup),
    }
    details = {
        "homeAbsentees": absentees(home_team_id, home_lineup),
        "awayAbsentees": absentees(away_team_id, away_lineup),
        "venue": {"name": venue, "spectators": "312", "city": "Château-d'Oex"},
        "referees": [],
        "linesmen": [],
        "homeTeam": team(home_team_id, home_name),
        "awayTeam": team(away_team_id, away_name),
    }
    result = (
        {
            "homeTeam": str(home_score),
            "awayTeam": str(away_score),
            "scores": [
                {
                    "homeTeam": str(home),
                    "awayTeam": str(away),
                    "name": f"{number}. Drittel",
                    "indicator": str(number),
                }
                for number, (home, away) in enumerate(period_scores, start=1)
            ],
            "sogs": [],
        }
        if played
        else {"homeTeam": "", "awayTeam": "", "scores": [], "sogs": []}
    )
    return {
        "summary": {"periods": [], "shootout": None},
        "lineUps": line_ups,
        "players": players,
        "stats": [],
        "round": 1,
        "league": {"id": 10, "name": "2. Liga"},
        "phase": {"id": 9001, "name": "Exhibition Games"},
        "gameId": game_id,
        "season": 2027,
        "result": result,
        "details": details,
        "startDateTime": start,
        "endDateTime": start,
        "status": {
            "id": 12 if played else 1,
            "percent": 100 if played else 0,
            "name": "Ende" if played else "Vorschau",
            "canceled": False,
            "type": "gameStatus",
        },
        "alias": "gameDetail",
    }


class FakeLeagueSource:
    """Stands in for the API adapter: returns a canned body, or raises the
    given exception."""

    def __init__(self, body=None) -> None:
        self._body = sihf_game_overview() if body is None else body
        self.requested_game_ids: list[str] = []

    def fetch_game_overview(self, game_id: str) -> dict[str, Any]:
        self.requested_game_ids.append(game_id)
        if isinstance(self._body, Exception):
            raise self._body
        return self._body
