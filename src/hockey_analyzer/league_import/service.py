"""`LeagueImportService` (ticket 21): turns a league (SIHF) game link into a
staged, unsaved `ImportProposal` -- which local `Team`s and `Player`s the
game's teams and roster rows would link to, and which would be created --
for the review screen (ticket 22) to present and the user to confirm as a
batch. Proposing never writes to the database: every lookup is a read, and
the proposal is plain data, not ORM objects (see CONTEXT.md's Team/Player
entries and ticket 11's import flow). Only `confirm` writes -- the
reviewed proposal, all of it, in a single commit.
"""

from __future__ import annotations

import enum
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as date_
from difflib import SequenceMatcher
from functools import partial
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from hockey_analyzer.domain.enums import Position, RinkType, Side
from hockey_analyzer.domain.game_setup import SameTeamBothSidesError
from hockey_analyzer.domain.models import Game, GameRosterEntry, Player, Team
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


class ManualEntryFallbackError(Exception):
    """Raised instead of returning a proposal when the game's PDF export
    couldn't be fetched or parsed. Nearly all of an
    import comes from it (ADR-0005), so there's nothing worth proposing:
    the caller should fall back to blank manual game/roster entry
    (ticket 14's `GameSetupService` path) rather than block game
    creation."""


class AlreadyImportedError(Exception):
    """Raised instead of importing a league game a local `Game` was already
    created from -- the caller should point the user at that game rather
    than create a duplicate (ticket 11)."""

    def __init__(self, existing_game_id: int) -> None:
        super().__init__(
            f"this league game is already imported as game {existing_game_id}"
        )
        self.existing_game_id = existing_game_id


class PlayerRosteredTwiceError(Exception):
    """Raised instead of linking the same existing `Player` to two roster
    rows of one import -- a player can't hold two roster spots in one
    game, so one of the two links is a matching mistake."""

    def __init__(self, player_id: int) -> None:
        super().__init__(f"player {player_id} is linked to more than one roster row")
        self.player_id = player_id


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
    """An existing `Team` the user may confirm a name match against."""

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
    """An existing `Player` the user may link a new-player row to instead,
    closest match first."""

    player_id: int
    full_name: str


@dataclass(frozen=True)
class RosterRowProposal:
    """One scraped lineup row, to become a `GameRosterEntry`. `position` is
    the game-of-the-day position for that entry only -- never a proposed
    write to `Player.position` (see CONTEXT.md's Game roster entry)."""

    side: Side
    jersey_number: int
    # As the export prints it -- "Last First" -- and as a new Player would
    # be created; matching is word-order tolerant only for candidates.
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

    def prefilled_user_team(self, team_ids: Mapping[Side, int | None]) -> Side | None:
        """The review screen's pre-filled answer to "which team is yours",
        given the team each side is currently set to link (`team_ids`):
        the side whose existing team is already flagged as the user's --
        a league_id link, or a name-match candidate the user confirmed --
        when exactly one is. Never a side creating a new team (ticket 11)."""
        flagged = [
            side
            for side in (Side.HOME, Side.AWAY)
            if (self.team(side).team_id is not None and self.team(side).is_user_team)
            or any(
                candidate.team_id == team_ids.get(side) and candidate.is_user_team
                for candidate in self.team(side).candidates
            )
        ]
        return flagged[0] if len(flagged) == 1 else None


@dataclass(frozen=True)
class ImportResolution:
    """The user's answers to an `ImportProposal`'s judgment calls, as the
    review screen submits them to `LeagueImportService.confirm`."""

    # Per side: the existing `Team` to link, or None to create a new one
    # from the proposal's scraped name/league_id.
    home_team_id: int | None
    away_team_id: int | None
    # Which side's team is the user's own -- always an explicit answer,
    # never inferred from home/away (ticket 11); None means neither. Only
    # ever sets the chosen team's `is_user_team`, never clears another's:
    # several teams may be flagged (CONTEXT.md's Team), so a per-game
    # answer is no reason to un-flag one.
    user_team: Side | None
    # One per `ImportProposal.roster` row, same order: the existing
    # `Player` to link, or None to create a new one from the row's name.
    player_ids: tuple[int | None, ...]
    # Set once at creation, like the manual path (ADR-0008) -- the league
    # site doesn't say which rink standard a game was played under.
    rink_type: RinkType = RinkType.IIHF

    @classmethod
    def as_proposed(cls, proposal: ImportProposal) -> ImportResolution:
        """Every proposed link taken as-is, anything unmatched created
        new, and `user_team` pre-filled from whichever linked team is
        already flagged as the user's -- only when that's unambiguous."""
        team_ids = {side: proposal.team(side).team_id for side in Side}
        return cls(
            home_team_id=team_ids[Side.HOME],
            away_team_id=team_ids[Side.AWAY],
            user_team=proposal.prefilled_user_team(team_ids),
            player_ids=tuple(row.player_id for row in proposal.roster),
        )

    def team_id(self, side: Side) -> int | None:
        return self.home_team_id if side == Side.HOME else self.away_team_id


class LeagueImportService:
    """Takes an already-open SQLAlchemy `Session` and a `LeagueSource`.
    `propose` only reads; `confirm` writes a reviewed proposal as one
    batch -- never write-then-edit (ticket 11's import flow)."""

    def __init__(self, db_session: Session, source: LeagueSource) -> None:
        self._db = db_session
        self._source = source

    def propose(self, game_link: str) -> ImportProposal:
        game_id = parse_game_link(game_link)
        try:
            scraped = parse_game_pdf(self._source.fetch_game_pdf(game_id))
        except LeagueSourceError as error:
            raise ManualEntryFallbackError(str(error)) from error
        if scraped.league_id != game_id:
            raise ManualEntryFallbackError(
                f"export is for game {scraped.league_id}, not {game_id}"
            )
        league_ids = self._fetch_team_league_ids(game_id)
        home, away = (
            self._resolve_team(side, scraped.team(side).name, league_ids.get(side))
            for side in (Side.HOME, Side.AWAY)
        )
        known_players = [
            (_normalize_name(player.full_name), player)
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

    def linkable_teams(self, league_id: str | None) -> tuple[TeamCandidate, ...]:
        """Every existing `Team` a side scraped with `league_id` could be
        linked to instead -- the review screen's override beyond the
        proposal's own candidates. A team already carrying a different
        league_id is provably another league team, so it's left out."""
        return tuple(
            TeamCandidate(team.id, team.name, team.is_user_team)
            for team in self._db.scalars(select(Team).order_by(Team.name))
            if team.league_id is None or team.league_id == league_id
        )

    def known_players(self) -> tuple[PlayerCandidate, ...]:
        """Every existing `Player`, for the review screen's searchable
        override of a roster row's link."""
        return tuple(
            PlayerCandidate(player.id, player.full_name or f"Player {player.id}")
            for player in self._db.scalars(select(Player).order_by(Player.full_name))
        )

    def confirm(self, proposal: ImportProposal, resolution: ImportResolution) -> Game:
        """Create the `Game`, any new `Team`s and `Player`s, and every
        `GameRosterEntry` exactly as reviewed, in a single commit: a
        refusal or failure anywhere writes nothing at all."""
        self._check_confirmable(proposal, resolution)
        try:
            teams = {
                side: self._confirmed_team(proposal.team(side), resolution)
                for side in (Side.HOME, Side.AWAY)
            }
            game = Game(
                league_id=proposal.league_id,
                date=proposal.date,
                venue=proposal.venue,
                home_score=proposal.home_score,
                away_score=proposal.away_score,
                period_scores=proposal.period_scores,
                rink_type=resolution.rink_type,
                home_team=teams[Side.HOME],
                away_team=teams[Side.AWAY],
            )
            self._db.add(game)
            for row, player_id in zip(
                proposal.roster, resolution.player_ids, strict=True
            ):
                # A new Player gets its name only: the row's position is
                # this game's lineup slot, never Player.position
                # (CONTEXT.md's Game roster entry).
                player = (
                    Player(full_name=row.full_name)
                    if player_id is None
                    else self._existing(Player, player_id)
                )
                self._db.add(
                    GameRosterEntry(
                        game=game,
                        player=player,
                        team=teams[row.side],
                        jersey_number=row.jersey_number,
                        position=row.position,
                    )
                )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        return game

    def _check_confirmable(
        self, proposal: ImportProposal, resolution: ImportResolution
    ) -> None:
        """The refusals detectable without adding anything to the
        session."""
        existing_game_id = self._db.scalar(
            select(Game.id).where(Game.league_id == proposal.league_id)
        )
        if existing_game_id is not None:
            raise AlreadyImportedError(existing_game_id)
        if len(resolution.player_ids) != len(proposal.roster):
            raise ValueError(
                f"expected {len(proposal.roster)} player choices, "
                f"got {len(resolution.player_ids)}"
            )
        home_id, away_id = resolution.home_team_id, resolution.away_team_id
        if home_id is not None and home_id == away_id:
            raise SameTeamBothSidesError(home_id)
        linked = Counter(p for p in resolution.player_ids if p is not None)
        for player_id, count in linked.items():
            if count > 1:
                raise PlayerRosteredTwiceError(player_id)

    def _confirmed_team(
        self, proposed: TeamProposal, resolution: ImportResolution
    ) -> Team:
        is_user_team = resolution.user_team == proposed.side
        team_id = resolution.team_id(proposed.side)
        if team_id is None:
            if proposed.league_id is not None and self._db.scalar(
                select(Team.id).where(Team.league_id == proposed.league_id)
            ):
                raise ValueError(
                    f"a team with league_id {proposed.league_id} already "
                    "exists; link it instead of creating a new one"
                )
            team = Team(
                name=proposed.name,
                league_id=proposed.league_id,
                is_user_team=is_user_team,
            )
            self._db.add(team)
            return team
        team = self._existing(Team, team_id)
        if proposed.league_id is not None:
            if team.league_id is None:
                # A confirmed name match: remember the link, so this
                # team's next import links silently by league_id.
                team.league_id = proposed.league_id
            elif team.league_id != proposed.league_id:
                raise ValueError(
                    f"team {team_id} is league team {team.league_id}, "
                    f"not {proposed.league_id}"
                )
        if is_user_team:
            team.is_user_team = True
        return team

    def _existing(self, model: type[Team] | type[Player], id_: int) -> Team | Player:
        row = self._db.get(model, id_)
        if row is None:
            raise KeyError(f"no {model.__name__} with id {id_}")
        return row

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
        key = _normalize_name(name)
        candidates = tuple(
            TeamCandidate(team.id, team.name, team.is_user_team)
            for team in self._db.scalars(select(Team).order_by(Team.id))
            if _normalize_name(team.name) == key
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
# A reordering of the same words ("First Last" vs the export's "Last First")
# ranks just below an exact match.
_REORDERED_NAME_SIMILARITY = 0.99


def _resolve_player(
    side: Side, scraped: ScrapedPlayer, known_players: list[tuple[str, Player]]
) -> RosterRowProposal:
    """Only a single exact (case/accent-insensitive) `full_name` match
    links silently; anything else defaults to a new `Player`, offering the
    closest existing ones as alternates -- matching players is a manual
    judgment call, never automated identity resolution (CONTEXT.md's
    Player entry)."""
    key = _normalize_name(scraped.full_name)
    exact = [player for known_key, player in known_players if known_key == key]
    proposed_row = partial(
        RosterRowProposal,
        side,
        scraped.jersey_number,
        scraped.full_name,
        scraped.position,
    )
    if len(exact) == 1:
        return proposed_row(PlayerMatchStatus.LINKED, player_id=exact[0].id)

    scored = []
    for known_key, player in known_players:
        similarity = SequenceMatcher(None, key, known_key).ratio()
        if sorted(known_key.split()) == sorted(key.split()):
            similarity = max(similarity, _REORDERED_NAME_SIMILARITY)
        if similarity >= _NEAR_MISS_RATIO:
            scored.append((-similarity, player.id, player))
    candidates = tuple(
        PlayerCandidate(player.id, player.full_name)
        for _, _, player in sorted(scored)[:_MAX_PLAYER_CANDIDATES]
    )
    return proposed_row(PlayerMatchStatus.NEW, candidates=candidates)


def _normalize_name(name: str) -> str:
    """Case-, accent- and spacing-insensitive form of a team or player
    name, the key both kinds of name matching compare on."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.casefold().split())
