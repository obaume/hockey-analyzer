"""`GameSetupService` (ticket 14): the fully manual path to create a
`Game` and populate its rosters, with no league import involved -- this is
both a standalone user-facing capability and the source of the
`game_id`/`home_team_id`/`away_team_id` that `TaggingSession` (ticket 15)
needs to exist before a tagging pass can start. PySide6 widgets call into
this rather than touching the ORM directly, the same split
`TaggingSession`'s module docstring describes.

Roster entry is deliberately side-symmetric: `add_roster_entry` takes
whichever `team_id` the caller passes, with no separate "opponent" method
or special-cased fields -- see CONTEXT.md's Game roster entry entry
("Applies uniformly to the home team and all other tracked teams").
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from hockey_analyzer.domain.enums import Position, RinkType, Side
from hockey_analyzer.domain.models import Game, GameRosterEntry, Player, Team


class SameTeamBothSidesError(Exception):
    """Raised instead of setting a `Game`'s home and away sides to the same
    `Team` -- pre-validated in Python, the same treatment
    `DuplicateJerseyNumberError` already gets, so the UI gets a catchable
    error instead of a raw `IntegrityError` from the
    `ck_game_home_away_distinct` constraint in models.py."""

    def __init__(self, team_id: int) -> None:
        super().__init__(f"team {team_id} cannot be both the home and away side of the same game")
        self.team_id = team_id


class DuplicateJerseyNumberError(Exception):
    """Raised instead of creating a second `GameRosterEntry` for a jersey
    number already taken within `(game, team)` -- CONTEXT.md's Game roster
    entry entry calls this a tagging error to reject, not tolerate, so it's
    caught here before any row (or an on-the-fly `Player`) is created,
    rather than left to surface as a bare `IntegrityError` from the
    `models.py` unique constraint."""

    def __init__(self, team_id: int, jersey_number: int) -> None:
        super().__init__(f"jersey number {jersey_number} is already on this team's roster for this game")
        self.team_id = team_id
        self.jersey_number = jersey_number


class GameSetupService:
    """Takes an already-open SQLAlchemy `Session`; every method here
    commits before returning, matching `TaggingSession`'s autosave
    treatment."""

    def __init__(self, db_session: Session) -> None:
        self._db = db_session

    # -- game -----------------------------------------------------------

    def create_game(self, *, rink_type: RinkType = RinkType.IIHF) -> Game:
        """No pre-existing data required -- a bare `Game` row, ready for
        `add_roster_entry` calls against it. `rink_type` is set here and
        only here: there is deliberately no method to change it afterward
        (see CONTEXT.md's Rink type entry and ADR-0008) -- picking the
        wrong one means deleting the game and starting over, not editing
        it in place."""
        game = Game(rink_type=rink_type)
        self._db.add(game)
        self._db.commit()
        return game

    def get_game(self, game_id: int) -> Game:
        game = self._db.get(Game, game_id)
        if game is None:
            raise KeyError(f"no game with id {game_id}")
        return game

    def list_games(self) -> list[Game]:
        """Most-recently-worked-on first (see `Game.updated_at` in
        models.py) -- the axis someone picking "which game am I resuming"
        is almost always scanning by."""
        return list(self._db.scalars(select(Game).order_by(Game.updated_at.desc())))

    def set_side_team(self, game_id: int, side: Side, team_id: int) -> Game:
        """Sets `game_id`'s home or away team -- side-symmetric like
        `add_roster_entry`, no separate "opponent" method (see CONTEXT.md's
        Game roster entry entry). Correctable at any time, unlike
        `rink_type` (see models.py's `Game.home_team_id` comment)."""
        game = self.get_game(game_id)
        other_team_id = game.away_team_id if side == "home" else game.home_team_id
        if other_team_id is not None and other_team_id == team_id:
            raise SameTeamBothSidesError(team_id)
        if side == "home":
            game.home_team_id = team_id
        else:
            game.away_team_id = team_id
        self._db.commit()
        return game

    def set_video_path(self, game_id: int, path: str) -> Game:
        """Attached on demand the first time footage is opened while this
        game is active, not required at game creation (see models.py's
        `Game.video_path` comment)."""
        game = self.get_game(game_id)
        game.video_path = path
        self._db.commit()
        return game

    # -- teams ------------------------------------------------------------

    def list_teams(self) -> list[Team]:
        return list(self._db.scalars(select(Team).order_by(Team.name)))

    def create_team(self, name: str, *, is_user_team: bool = False) -> Team:
        team = Team(name=name, is_user_team=is_user_team)
        self._db.add(team)
        self._db.commit()
        return team

    # -- players ----------------------------------------------------------

    def list_players(self) -> list[Player]:
        return list(self._db.scalars(select(Player).order_by(Player.full_name)))

    def get_player(self, player_id: int) -> Player:
        return self._get_player(player_id)

    def create_player(self, *, full_name: str | None = None, position: Position | None = None) -> Player:
        """`full_name` is promptable but skippable at creation (see
        CONTEXT.md's Player entry) -- omit it and backfill later with
        `set_player_full_name`."""
        player = Player(full_name=full_name, position=position)
        self._db.add(player)
        self._db.commit()
        return player

    def set_player_full_name(self, player_id: int, full_name: str | None) -> Player:
        player = self._get_player(player_id)
        player.full_name = full_name
        self._db.commit()
        return player

    def set_player_position(self, player_id: int, position: Position | None) -> Player:
        """A fixed attribute of the `Player` record, set once, manually --
        not per game (see CONTEXT.md's Position entry)."""
        player = self._get_player(player_id)
        player.position = position
        self._db.commit()
        return player

    def _get_player(self, player_id: int) -> Player:
        player = self._db.get(Player, player_id)
        if player is None:
            raise KeyError(f"no player with id {player_id}")
        return player

    # -- roster -----------------------------------------------------------

    def add_roster_entry(
        self,
        *,
        game_id: int,
        team_id: int,
        jersey_number: int,
        player_id: int | None = None,
        full_name: str | None = None,
        position: Position | None = None,
    ) -> GameRosterEntry:
        """Add a `Player` to `team_id`'s roster for `game_id`. Pass an
        existing `player_id` to roster a known player, or omit it to
        create a brand-new `Player` on the spot from `full_name`/
        `position` (both optional). `team_id` may be either side of the
        game -- there is no separate "opponent" variant of this method."""
        if player_id is not None and (full_name is not None or position is not None):
            raise ValueError("full_name/position only apply when creating a new player, not when player_id is given")

        if self._roster_entry(game_id, team_id, jersey_number) is not None:
            raise DuplicateJerseyNumberError(team_id, jersey_number)

        if player_id is None:
            player = self.create_player(full_name=full_name, position=position)
            player_id = player.id

        entry = GameRosterEntry(game_id=game_id, player_id=player_id, team_id=team_id, jersey_number=jersey_number)
        self._db.add(entry)
        self._db.commit()
        return entry

    def list_roster(self, game_id: int, team_id: int) -> list[GameRosterEntry]:
        stmt = (
            select(GameRosterEntry)
            .where(GameRosterEntry.game_id == game_id, GameRosterEntry.team_id == team_id)
            .order_by(GameRosterEntry.jersey_number)
        )
        return list(self._db.scalars(stmt))

    def _roster_entry(self, game_id: int, team_id: int, jersey_number: int) -> GameRosterEntry | None:
        stmt = select(GameRosterEntry).where(
            GameRosterEntry.game_id == game_id,
            GameRosterEntry.team_id == team_id,
            GameRosterEntry.jersey_number == jersey_number,
        )
        return self._db.scalars(stmt).one_or_none()
