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

from collections.abc import Callable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from hockey_analyzer.domain.enums import EventType, Position, RinkType, Side, UnitType
from hockey_analyzer.domain.models import (
    PLAYER_REFERENCE_FIELDS,
    Event,
    Game,
    GameRosterEntry,
    GameUnitAssignment,
    Player,
    Team,
    involved_players,
)
from hockey_analyzer.domain.tagging_session import roster_entry_label
from hockey_analyzer.domain.video_timestamp import format_video_timestamp


class SameTeamBothSidesError(Exception):
    """Raised instead of setting a `Game`'s home and away sides to the same
    `Team` -- pre-validated in Python, the same treatment
    `DuplicateJerseyNumberError` already gets, so the UI gets a catchable
    error instead of a raw `IntegrityError` from the
    `ck_game_home_away_distinct` constraint in models.py."""

    def __init__(self, team_id: int) -> None:
        super().__init__(
            f"team {team_id} cannot be both the home and away side of the same game"
        )
        self.team_id = team_id


class DuplicateJerseyNumberError(Exception):
    """Raised instead of creating a second `GameRosterEntry` for a jersey
    number already taken within `(game, team)` -- CONTEXT.md's Game roster
    entry entry calls this a tagging error to reject, not tolerate, so it's
    caught here before any row (or an on-the-fly `Player`) is created,
    rather than left to surface as a bare `IntegrityError` from the
    `models.py` unique constraint."""

    def __init__(self, team_id: int, jersey_number: int) -> None:
        super().__init__(
            f"jersey number {jersey_number} is already on this team's roster for this game"
        )
        self.team_id = team_id
        self.jersey_number = jersey_number


class PlayerNotRosteredError(Exception):
    """Raised instead of assigning a `Player` to a unit of a team whose
    roster for that game doesn't include them -- a unit is a subset of one
    team's game roster (see CONTEXT.md's Game unit assignment entry), so
    an assignment pointing outside it would be silently unreachable from
    that team's line changes."""

    def __init__(self, game_id: int, team_id: int, player_id: int) -> None:
        super().__init__(
            f"player {player_id} is not on team {team_id}'s roster for game {game_id}"
        )
        self.game_id = game_id
        self.team_id = team_id
        self.player_id = player_id


def _event_list(events: Sequence[Event]) -> str:
    """e.g. "1 event — change it first: shot attempt @ 0:12:34" -- the
    tail of a roster-correction refusal, naming each event by type and
    video timestamp so the tagger can find and fix it."""
    count = f"{len(events)} event" if len(events) == 1 else f"{len(events)} events"
    pronoun = "it" if len(events) == 1 else "them"
    listed = ", ".join(
        f"{EventType(event.event_type).value.replace('_', ' ')} "
        f"@ {format_video_timestamp(event.video_timestamp)}"
        for event in events
    )
    return f"{count} — change {pronoun} first: {listed}"


class RosterEntryInUseError(Exception):
    """Raised instead of removing a `GameRosterEntry` whose player an
    event in that game still references -- removal never rewrites or
    drops a tagged event (see CONTEXT.md's Game roster entry entry), so
    `events` (in video order) have to be changed first."""

    def __init__(self, entry_label: str, events: Sequence[Event]) -> None:
        super().__init__(f"{entry_label} is used by {_event_list(events)}")
        self.entry_label = entry_label
        self.events = list(events)


class SharedEventsError(Exception):
    """Raised instead of merging two roster entries while an event
    references both players (e.g. one shoots, the other assists):
    repointing it would leave the merged player in two roles of one event,
    so `events` (in video order) have to be changed first."""

    def __init__(
        self, source_label: str, target_label: str, events: Sequence[Event]
    ) -> None:
        super().__init__(
            f"{source_label} and {target_label} are both on {_event_list(events)}"
        )
        self.source_label = source_label
        self.target_label = target_label
        self.events = list(events)


class GameSetupService:
    """Takes an already-open SQLAlchemy `Session`; every method here
    commits before returning, matching `TaggingSession`'s autosave
    treatment.

    `on_save` is told, in a few words (e.g. "units changed"), about every
    save that changes a game's setup -- the main window clears the
    tagging undo history on it (#64), since undoing a tagging step across
    a setup change could clash with it. Attaching footage isn't one: it
    touches nothing a tagging step does."""

    def __init__(
        self,
        db_session: Session,
        *,
        on_save: Callable[[str], None] | None = None,
    ) -> None:
        self._db = db_session
        self._on_save = on_save

    def _commit_and_report(self, reason: str) -> None:
        self._db.commit()
        if self._on_save is not None:
            self._on_save(reason)

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
        self._commit_and_report("game created")
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
        self._commit_and_report("game setup changed")
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
        self._commit_and_report("team created")
        return team

    # -- players ----------------------------------------------------------

    def list_players(self) -> list[Player]:
        return list(self._db.scalars(select(Player).order_by(Player.full_name)))

    def get_player(self, player_id: int) -> Player:
        return self._get_player(player_id)

    def create_player(
        self, *, full_name: str | None = None, position: Position | None = None
    ) -> Player:
        """`full_name` is promptable but skippable at creation (see
        CONTEXT.md's Player entry) -- omit it and backfill later with
        `set_player_full_name`."""
        player = Player(full_name=full_name, position=position)
        self._db.add(player)
        self._commit_and_report("player created")
        return player

    def set_player_full_name(self, player_id: int, full_name: str | None) -> Player:
        player = self._get_player(player_id)
        player.full_name = full_name
        self._commit_and_report("player changed")
        return player

    def set_player_position(self, player_id: int, position: Position | None) -> Player:
        """A fixed attribute of the `Player` record, set once, manually --
        not per game (see CONTEXT.md's Position entry)."""
        player = self._get_player(player_id)
        player.position = position
        self._commit_and_report("player changed")
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
            raise ValueError(
                "full_name/position only apply when creating a new player, not when player_id is given"
            )

        if self.find_roster_entry(game_id, team_id, jersey_number) is not None:
            raise DuplicateJerseyNumberError(team_id, jersey_number)

        if player_id is None:
            player = self.create_player(full_name=full_name, position=position)
            player_id = player.id

        entry = GameRosterEntry(
            game_id=game_id,
            player_id=player_id,
            team_id=team_id,
            jersey_number=jersey_number,
        )
        self._db.add(entry)
        self._commit_and_report("roster changed")
        return entry

    def list_roster(self, game_id: int, team_id: int) -> list[GameRosterEntry]:
        stmt = (
            select(GameRosterEntry)
            .where(
                GameRosterEntry.game_id == game_id, GameRosterEntry.team_id == team_id
            )
            .order_by(GameRosterEntry.jersey_number)
        )
        return list(self._db.scalars(stmt))

    def find_roster_entry(
        self, game_id: int, team_id: int, jersey_number: int
    ) -> GameRosterEntry | None:
        """Whoever wears `jersey_number` on `team_id`'s roster for
        `game_id`, if anyone -- e.g. the entry a renumber onto a taken
        number would merge into."""
        stmt = select(GameRosterEntry).where(
            GameRosterEntry.game_id == game_id,
            GameRosterEntry.team_id == team_id,
            GameRosterEntry.jersey_number == jersey_number,
        )
        return self._db.scalars(stmt).one_or_none()

    # -- correcting a roster entry after the fact (#89) --------------------
    #
    # Chiefly to clean up an entry created on the fly from a mistyped
    # jersey number (see CONTEXT.md's Game roster entry entry). Each one
    # commits on its own, like every other save here.

    def get_roster_entry(self, entry_id: int) -> GameRosterEntry:
        entry = self._db.get(GameRosterEntry, entry_id)
        if entry is None:
            raise KeyError(f"no roster entry with id {entry_id}")
        return entry

    def referencing_events(self, game_id: int, player_id: int) -> list[Event]:
        """Every event in `game_id` naming `player_id` in any role, in
        video order."""
        stmt = (
            select(Event)
            .where(Event.game_id == game_id)
            .order_by(Event.video_timestamp, Event.id)
        )
        return [
            event
            for event in self._db.scalars(stmt)
            if player_id in involved_players(event)
        ]

    def renumber_roster_entry(
        self, entry_id: int, jersey_number: int
    ) -> GameRosterEntry:
        """Change an entry's jersey number. Events reference the `Player`,
        not the number, so every one of them follows. A number already
        taken on that team's roster for that game raises
        `DuplicateJerseyNumberError` -- `merge_roster_entry` is the way to
        fold the entry into whoever holds it."""
        entry = self.get_roster_entry(entry_id)
        if entry.jersey_number == jersey_number:
            return entry
        if (
            self.find_roster_entry(entry.game_id, entry.team_id, jersey_number)
            is not None
        ):
            raise DuplicateJerseyNumberError(entry.team_id, jersey_number)
        entry.jersey_number = jersey_number
        self._commit_and_report("roster changed")
        return entry

    def remove_roster_entry(self, entry_id: int) -> None:
        """Take an entry off its game's roster, along with that player's
        unit assignments for the game, and the `Player` too if this was
        their only roster entry in any game. Refused with
        `RosterEntryInUseError` while any event in that game references
        the player."""
        self.check_removable(entry_id)
        self._delete_roster_entry(self.get_roster_entry(entry_id))
        self._commit_and_report("roster changed")

    def check_removable(self, entry_id: int) -> None:
        """Raises the `RosterEntryInUseError` `remove_roster_entry` would,
        without writing anything -- so a refusal can be shown before
        asking to confirm."""
        entry = self.get_roster_entry(entry_id)
        events = self.referencing_events(entry.game_id, entry.player_id)
        if events:
            raise RosterEntryInUseError(roster_entry_label(entry), events)

    def merge_roster_entry(self, source_entry_id: int, target_entry_id: int) -> None:
        """Fold the source entry into the target, both on the same team's
        roster for the same game: every reference to the source's player
        in that game's events is repointed to the target's player, then
        the source is removed as `remove_roster_entry` would. The source's
        unit assignments are dropped, never carried over. Refused with
        `SharedEventsError` while any event names both players."""
        events = self.merge_preview(source_entry_id, target_entry_id)
        source = self.get_roster_entry(source_entry_id)
        target = self.get_roster_entry(target_entry_id)
        for event in events:
            for name in PLAYER_REFERENCE_FIELDS[type(event)]:
                if getattr(event, name) == source.player_id:
                    setattr(event, name, target.player_id)
        self._delete_roster_entry(source)
        self._commit_and_report("roster changed")

    def merge_preview(self, source_entry_id: int, target_entry_id: int) -> list[Event]:
        """The events `merge_roster_entry` would repoint, in video order,
        without writing anything -- or the `SharedEventsError` (or
        `ValueError`) it would raise."""
        source = self.get_roster_entry(source_entry_id)
        target = self.get_roster_entry(target_entry_id)
        if (source.game_id, source.team_id) != (target.game_id, target.team_id):
            raise ValueError("can only merge entries on the same team in the same game")
        if source.id == target.id:
            raise ValueError("can't merge a roster entry into itself")

        events = self.referencing_events(source.game_id, source.player_id)
        shared = [
            event for event in events if target.player_id in involved_players(event)
        ]
        if shared:
            raise SharedEventsError(
                roster_entry_label(source), roster_entry_label(target), shared
            )
        return events

    def _delete_roster_entry(self, entry: GameRosterEntry) -> None:
        """Deletes without committing, so a merge's repointing and its
        removal land in one commit."""
        assignments = select(GameUnitAssignment).where(
            GameUnitAssignment.game_id == entry.game_id,
            GameUnitAssignment.player_id == entry.player_id,
        )
        for assignment in self._db.scalars(assignments):
            self._db.delete(assignment)
        player_id = entry.player_id
        self._db.delete(entry)
        # Flushed first so the Player's (now empty) roster/unit collections
        # don't try to null out the rows just deleted.
        self._db.flush()
        other_entries = select(GameRosterEntry.id).where(
            GameRosterEntry.player_id == player_id
        )
        if self._db.scalars(other_entries).first() is None:
            player = self._get_player(player_id)
            self._db.expire(player, ["roster_entries", "unit_assignments"])
            self._db.delete(player)

    # -- unit assignments -------------------------------------------------

    def assign_unit(
        self,
        *,
        game_id: int,
        team_id: int,
        player_id: int,
        unit_type: UnitType,
        unit_number: int,
    ) -> GameUnitAssignment:
        """Put `player_id` on `unit_type` #`unit_number` for the whole of
        `game_id` (see CONTEXT.md's Game unit assignment entry). A player
        holds at most one unit per `unit_type` but may hold several unit
        types at once, so assigning a type they already hold moves them to
        the new number rather than adding a second row."""
        if unit_number < 1:
            raise ValueError(f"unit_number must be 1 or greater, got {unit_number}")
        roster_stmt = select(GameRosterEntry).where(
            GameRosterEntry.game_id == game_id,
            GameRosterEntry.team_id == team_id,
            GameRosterEntry.player_id == player_id,
        )
        if self._db.scalars(roster_stmt).first() is None:
            raise PlayerNotRosteredError(game_id, team_id, player_id)

        assignment = self._unit_assignment(game_id, player_id, unit_type)
        if assignment is None:
            assignment = GameUnitAssignment(
                game_id=game_id, player_id=player_id, unit_type=unit_type
            )
            self._db.add(assignment)
        assignment.team_id = team_id
        assignment.unit_number = unit_number
        self._commit_and_report("units changed")
        return assignment

    def unassign_unit(
        self, *, game_id: int, player_id: int, unit_type: UnitType
    ) -> None:
        """Take `player_id` off whichever `unit_type` unit they hold in
        `game_id`, if any -- a no-op when they hold none."""
        assignment = self._unit_assignment(game_id, player_id, unit_type)
        if assignment is None:
            return
        self._db.delete(assignment)
        self._commit_and_report("units changed")

    def set_player_units(
        self,
        *,
        game_id: int,
        team_id: int,
        player_id: int,
        units: Mapping[UnitType, int | None],
    ) -> None:
        """Set several of `player_id`'s units in one go: a number assigns
        (or moves) them, `None` takes them off that unit type. Unit types
        absent from `units` are left as they are."""
        for unit_type, unit_number in units.items():
            if unit_number is None:
                self.unassign_unit(
                    game_id=game_id, player_id=player_id, unit_type=unit_type
                )
            else:
                self.assign_unit(
                    game_id=game_id,
                    team_id=team_id,
                    player_id=player_id,
                    unit_type=unit_type,
                    unit_number=unit_number,
                )

    def player_unit_assignments(
        self, game_id: int, player_id: int
    ) -> dict[UnitType, int]:
        """Every unit `player_id` holds in `game_id`, as unit_type -> unit
        number."""
        stmt = select(GameUnitAssignment).where(
            GameUnitAssignment.game_id == game_id,
            GameUnitAssignment.player_id == player_id,
        )
        return {
            UnitType(assignment.unit_type): assignment.unit_number
            for assignment in self._db.scalars(stmt)
        }

    def unit_numbers(
        self, *, game_id: int, team_id: int, unit_type: UnitType
    ) -> dict[int, int]:
        """player id -> unit number, for every one of `team_id`'s players
        holding a `unit_type` unit in `game_id`. One query answers both
        "who is on Forward line 1" and "which line is everyone else on"."""
        stmt = select(
            GameUnitAssignment.player_id, GameUnitAssignment.unit_number
        ).where(
            GameUnitAssignment.game_id == game_id,
            GameUnitAssignment.team_id == team_id,
            GameUnitAssignment.unit_type == unit_type,
        )
        return {
            player_id: unit_number for player_id, unit_number in self._db.execute(stmt)
        }

    def _unit_assignment(
        self, game_id: int, player_id: int, unit_type: UnitType
    ) -> GameUnitAssignment | None:
        stmt = select(GameUnitAssignment).where(
            GameUnitAssignment.game_id == game_id,
            GameUnitAssignment.player_id == player_id,
            GameUnitAssignment.unit_type == unit_type,
        )
        return self._db.scalars(stmt).one_or_none()
