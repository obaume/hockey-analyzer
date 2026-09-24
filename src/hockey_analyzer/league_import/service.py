"""`LeagueImportService` (ticket 21): turns a league (SIHF) game link into a
staged, unsaved `ImportProposal` -- which local `Team`s and `Player`s the
game's teams and roster rows would link to, and which would be created --
for the review screen (ticket 22) to present and the user to confirm as a
batch. Nothing here writes to the database: every lookup is a read, and
the proposal is plain data, not ORM objects (see CONTEXT.md's Team/Player
entries and ticket 11's import flow).
"""

from __future__ import annotations

import enum
import unicodedata
from dataclasses import dataclass, field
from datetime import date as date_
from difflib import SequenceMatcher
from functools import partial
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from hockey_analyzer.domain.enums import Position, Side
from hockey_analyzer.domain.models import Game, Player, Team
from hockey_analyzer.league_import.sihf import (
    LeagueSourceError,
    ScrapedPlayer,
    parse_game_link,
    parse_game_pdf,
    parse_team_league_ids,
)


class LeagueSource(Protocol):
    """Fetches the two league-site documents for a game ID. Raises
    `LeagueSourceError` on any failure."""

    def fetch_game_pdf(self, game_id: str) -> bytes: ...

    def fetch_game_page(self, game_id: str) -> str: ...


class ManualEntryFallback(Exception):
    """The game's PDF export couldn't be fetched or parsed. Nearly all of an
    import comes from it (ADR-0005), so there's nothing worth proposing:
    the caller should fall back to blank manual game/roster entry
    (ticket 14's `GameSetupService` path) rather than block game
    creation."""


class TeamMatchStatus(enum.StrEnum):
    # Exact league_id match: the only case ever linked without asking.
    LINKED = "linked"
    # Same name, no league_id match: a candidate the user must confirm
    # before it's linked (never silently merged -- ticket 11).
    NEEDS_CONFIRMATION = "needs_confirmation"
    # No match: create a new Team from the scraped name + league_id.
    NEW = "new"


@dataclass(frozen=True)
class TeamCandidate:
    team_id: int
    name: str
    is_user_team: bool


@dataclass(frozen=True)
class TeamProposal:
    """One side's team as the import would resolve it. `team_id` is set
    only when `LINKED`; otherwise the proposal is to create a new `Team`
    from `name`/`league_id`, unless the user picks one of `candidates`.

    `is_user_team` is the linked team's current flag, for pre-filling the
    review screen's "which team is yours" choice -- always False for a new
    team, which never gets the flag set automatically (ticket 11)."""

    side: Side
    name: str
    league_id: str | None
    status: TeamMatchStatus
    team_id: int | None = None
    is_user_team: bool = False
    candidates: tuple[TeamCandidate, ...] = field(default_factory=tuple)


class PlayerMatchStatus(enum.StrEnum):
    # Exactly one existing Player with the same full_name (case/accent
    # insensitive): linked without asking.
    LINKED = "linked"
    # Anything else -- near-miss, several candidates, or none: create a new
    # Player unless the user overrides with one of the candidates.
    NEW = "new"


@dataclass(frozen=True)
class PlayerCandidate:
    player_id: int
    full_name: str


@dataclass(frozen=True)
class RosterRowProposal:
    """One scraped lineup row, to become a `GameRosterEntry`. `position` is
    the game-of-the-day position for that entry only -- never a proposed
    write to `Player.position` (see CONTEXT.md's Game roster entry)."""

    side: Side
    jersey_number: int
    full_name: str
    position: Position
    status: PlayerMatchStatus
    player_id: int | None = None
    candidates: tuple[PlayerCandidate, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ImportProposal:
    """Everything a confirmed import would write, as plain data: the
    `Game` fields, both sides' teams, and every roster row."""

    # The league site's game ID -- Game.league_id.
    league_id: str
    date: date_ | None
    venue: str | None
    home_score: int
    away_score: int
    period_scores: list[dict[str, int]]
    # The local Game already imported from this league game, if any -- a
    # second import of it is to be refused, pointing here instead.
    existing_game_id: int | None
    home: TeamProposal
    away: TeamProposal
    # False when the game page (the only source of team league_ids) was
    # lost: teams were then matched by name only, and a new Team would be
    # created without a league_id.
    team_league_ids_found: bool
    # Both teams' rows, home first, each in the export's listing order.
    roster: tuple[RosterRowProposal, ...]

    def team(self, side: Side) -> TeamProposal:
        return self.home if side == Side.HOME else self.away

    def roster_for(self, side: Side) -> list[RosterRowProposal]:
        return [row for row in self.roster if row.side == side]


class LeagueImportService:
    def __init__(self, db_session: Session, source: LeagueSource) -> None:
        self._db = db_session
        self._source = source

    def propose(self, game_link: str) -> ImportProposal:
        game_id = parse_game_link(game_link)
        try:
            scraped = parse_game_pdf(self._source.fetch_game_pdf(game_id))
        except LeagueSourceError as error:
            raise ManualEntryFallback(str(error)) from error
        if scraped.league_id != game_id:
            raise ManualEntryFallback(
                f"export is for game {scraped.league_id}, not {game_id}"
            )
        league_ids = self._fetch_team_league_ids(game_id)
        home, away = (
            self._resolve_team(side, scraped.team(side).name, league_ids.get(side))
            for side in (Side.HOME, Side.AWAY)
        )
        known_players = [
            (normalize_name(player.full_name), player)
            for player in self._db.scalars(select(Player).order_by(Player.id))
            if player.full_name
        ]
        roster = tuple(
            _resolve_player(side, scraped_player, known_players)
            for side in (Side.HOME, Side.AWAY)
            for scraped_player in scraped.team(side).lineup
        )
        return ImportProposal(
            league_id=scraped.league_id,
            date=scraped.date,
            venue=scraped.venue,
            home_score=scraped.home_score,
            away_score=scraped.away_score,
            period_scores=scraped.period_scores,
            existing_game_id=self._db.scalar(
                select(Game.id).where(Game.league_id == scraped.league_id)
            ),
            home=home,
            away=away,
            team_league_ids_found=bool(league_ids),
            roster=roster,
        )

    def _fetch_team_league_ids(self, game_id: str) -> dict[Side, str]:
        """Empty if the game page is lost -- it only ever contributes team
        league_ids, so losing it degrades team matching to name-only
        instead of failing the import (ADR-0005)."""
        try:
            return parse_team_league_ids(self._source.fetch_game_page(game_id))
        except LeagueSourceError:
            return {}

    def _resolve_team(
        self, side: Side, name: str, league_id: str | None
    ) -> TeamProposal:
        if league_id is not None:
            team = self._db.scalar(select(Team).where(Team.league_id == league_id))
            if team is not None:
                return TeamProposal(
                    side,
                    name,
                    league_id,
                    TeamMatchStatus.LINKED,
                    team_id=team.id,
                    is_user_team=team.is_user_team,
                )
        key = normalize_name(name)
        candidates = tuple(
            TeamCandidate(team.id, team.name, team.is_user_team)
            for team in self._db.scalars(select(Team).order_by(Team.id))
            if normalize_name(team.name) == key
            # With the scraped league_id known, a team already carrying a
            # different one is provably a different league team, whatever
            # its name.
            and (league_id is None or team.league_id is None)
        )
        status = (
            TeamMatchStatus.NEEDS_CONFIRMATION if candidates else TeamMatchStatus.NEW
        )
        return TeamProposal(side, name, league_id, status, candidates=candidates)


# How many alternate matches a new-player row offers. The review screen's
# searchable override covers anything further down the list.
_MAX_PLAYER_CANDIDATES = 5
# difflib ratio above which two normalized names count as a near miss
# (a typo or a dropped/doubled letter, e.g. "Mathias"/"Matthias").
_NEAR_MISS_RATIO = 0.85


def _resolve_player(
    side: Side, scraped: ScrapedPlayer, known_players: list[tuple[str, Player]]
) -> RosterRowProposal:
    """Only a single exact (case/accent-insensitive) `full_name` match
    links silently; anything else defaults to a new `Player`, offering the
    closest existing ones as alternates -- matching players is a manual
    judgment call, never automated identity resolution (CONTEXT.md's
    Player entry)."""
    key = normalize_name(scraped.full_name)
    exact = [player for known_key, player in known_players if known_key == key]
    row = partial(
        RosterRowProposal,
        side,
        scraped.jersey_number,
        scraped.full_name,
        scraped.position,
    )
    if len(exact) == 1:
        return row(PlayerMatchStatus.LINKED, player_id=exact[0].id)

    scored = []
    for known_key, player in known_players:
        similarity = SequenceMatcher(None, key, known_key).ratio()
        # The same words in another order ("First Last" vs the export's
        # "Last First") is as close as a near miss gets short of exact.
        if sorted(known_key.split()) == sorted(key.split()):
            similarity = max(similarity, 0.99)
        if similarity >= _NEAR_MISS_RATIO:
            scored.append((-similarity, player.id, player))
    candidates = tuple(
        PlayerCandidate(player.id, player.full_name)
        for _, _, player in sorted(scored)[:_MAX_PLAYER_CANDIDATES]
    )
    return row(PlayerMatchStatus.NEW, candidates=candidates)


def normalize_name(name: str) -> str:
    """Case-, accent- and spacing-insensitive form of a team or player
    name, the key both kinds of name matching compare on."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.casefold().split())
