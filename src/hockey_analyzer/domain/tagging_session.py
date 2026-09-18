"""`TaggingSession` (ticket 15): the service that owns every state change
for one `Game`'s live tagging pass. PySide6 widgets call into this rather
than touching the ORM directly (see CONTEXT.md's Event entry and ticket
08's manual-tagging answer, which this implements).

Two-phase event capture is the core workflow: `log_event` records an
event's `video_timestamp` the instant a hotkey/click fires, without
pausing playback and without requiring any of the type-specific detail
fields yet (any player reference a subtype's CHECK constraint requires --
see models.py -- defaults to explicit `unknown` so the stub still commits
cleanly). The tagger fills in the rest afterward through `update_event`/
`set_player_reference`, at their own pace, from the always-visible event
log. Every mutation here commits immediately -- that commit *is* the
session's autosave, not a separate feature -- so a session survives a
restart with no data loss.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from hockey_analyzer.domain.enums import EventType
from hockey_analyzer.domain.models import (
    Event,
    GameRosterEntry,
    Penalty,
    PeriodEnd,
    PeriodStart,
    Player,
    ShiftChange,
    Stoppage,
    Team,
)

# The event types this ticket covers -- the ones that don't need a
# rink-coordinate click. faceoff/shot_attempt are ticket 16's.
_MODEL_BY_TYPE: dict[EventType, type[Event]] = {
    EventType.PERIOD_START: PeriodStart,
    EventType.PERIOD_END: PeriodEnd,
    EventType.STOPPAGE: Stoppage,
    EventType.PENALTY: Penalty,
    EventType.SHIFT_CHANGE: ShiftChange,
}

# Each in-scope subtype's required player-reference columns, as
# (id_column, unknown_column) -- used both to default a fresh stub's
# reference to explicit "unknown" (models.py's CHECK constraint demands
# exactly one of the pair be set) and to resolve a jersey number into the
# right columns later.
_PLAYER_REFERENCE_FIELDS: dict[EventType, tuple[str, str]] = {
    EventType.PENALTY: ("penalty_player_id", "penalty_player_unknown"),
    EventType.SHIFT_CHANGE: ("shift_player_id", "shift_player_unknown"),
}

_TEAM_FIELD: dict[EventType, str] = {
    EventType.PENALTY: "penalty_team_id",
    EventType.SHIFT_CHANGE: "shift_team_id",
}

TeamSide = Literal["home", "away"]

# Public: the widget layer needs the same set to decide which event types
# show a player-reference field, without redeclaring it independently.
EVENT_TYPES_WITH_PLAYER_REFERENCE = frozenset(_PLAYER_REFERENCE_FIELDS)


class TaggingSession:
    """Owns all state changes for one `Game`'s tagging pass, scoped to two
    sides (`home_team_id`/`away_team_id`) so a tagger can identify a
    player with a single team-scope hotkey ("h"/"a") plus jersey number,
    per ticket 08. Takes an already-open SQLAlchemy `Session`; every
    method here commits before returning."""

    def __init__(self, db_session: Session, *, game_id: int, home_team_id: int, away_team_id: int) -> None:
        self._db = db_session
        self.game_id = game_id
        self.home_team_id = home_team_id
        self.away_team_id = away_team_id

    # -- event CRUD -------------------------------------------------

    def log_event(
        self, event_type: EventType, video_timestamp: int, *, strength_state: str | None = None
    ) -> Event:
        """Instantly capture an event's timestamp -- see module docstring.
        `strength_state` defaults to the computed on-ice-count guess
        (`_infer_strength_state`) when omitted, but is always explicitly
        overridable, including right here at capture time."""
        model = _MODEL_BY_TYPE[event_type]
        fields: dict[str, object] = {}
        reference = _PLAYER_REFERENCE_FIELDS.get(event_type)
        if reference is not None:
            _, unknown_column = reference
            fields[unknown_column] = True

        event = model(
            game_id=self.game_id,
            video_timestamp=video_timestamp,
            strength_state=(
                strength_state if strength_state is not None else self._infer_strength_state(video_timestamp)
            ),
            **fields,
        )
        self._db.add(event)
        self._db.commit()
        return event

    def update_event(self, event_id: int, **fields: object) -> Event:
        """Edit any field on an already-logged event. The event log is
        edit-anytime, not append-only or strictly sequential (see
        CONTEXT.md and ticket 08) -- there is no separate "commit" step
        beyond this call."""
        event = self._get(event_id)
        valid_columns = {column.key for column in inspect(event).mapper.columns}
        for name, value in fields.items():
            if name not in valid_columns:
                raise ValueError(f"{type(event).__name__} has no field {name!r}")
            setattr(event, name, value)
        self._db.commit()
        return event

    def delete_event(self, event_id: int) -> None:
        event = self._get(event_id)
        self._db.delete(event)
        self._db.commit()

    def list_events(self) -> list[Event]:
        """Every event for this session's game, in the order the
        always-visible event log displays them: by video timestamp,
        tiebroken by insertion order for events logged at the same
        instant."""
        stmt = select(Event).where(Event.game_id == self.game_id).order_by(Event.video_timestamp, Event.id)
        return list(self._db.scalars(stmt))

    def get_event(self, event_id: int) -> Event:
        return self._get(event_id)

    def _get(self, event_id: int) -> Event:
        event = self._db.get(Event, event_id)
        if event is None:
            raise KeyError(f"no event with id {event_id}")
        return event

    def describe_event(self, event: Event) -> str:
        """A short, human-readable summary line for the always-visible
        event log -- kept here rather than in the widget layer, matching
        the module docstring's "widgets only render" split."""
        event_type = EventType(event.event_type)
        if event_type in (EventType.PERIOD_START, EventType.PERIOD_END):
            return f"Period {event.period_number}" if event.period_number else "period not set"
        if event_type is EventType.STOPPAGE:
            return ""
        if event_type is EventType.PENALTY:
            who = self._describe_player_reference(
                event.penalty_team_id, event.penalty_player_id, event.penalty_player_unknown
            )
            duration = f"{event.penalty_duration_minutes:g} min" if event.penalty_duration_minutes else "duration not set"
            infraction = event.penalty_infraction or "infraction not set"
            return f"{who} - {infraction} ({duration})"
        if event_type is EventType.SHIFT_CHANGE:
            who = self._describe_player_reference(
                event.shift_team_id, event.shift_player_id, event.shift_player_unknown
            )
            if event.shift_on_ice is None:
                state = "on/off not set"
            else:
                state = "ON" if event.shift_on_ice else "OFF"
            return f"{who} {state}"
        return ""

    def _describe_player_reference(self, team_id: int | None, player_id: int | None, unknown: bool) -> str:
        team = self._db.get(Team, team_id) if team_id is not None else None
        team_label = team.name if team is not None else "team not set"
        if unknown:
            return f"{team_label} - unknown player"
        if player_id is None:
            return f"{team_label} - player not set"

        stmt = select(GameRosterEntry).where(
            GameRosterEntry.game_id == self.game_id, GameRosterEntry.player_id == player_id
        )
        entry = self._db.scalars(stmt).one_or_none()
        jersey = f"#{entry.jersey_number}" if entry is not None else ""
        player = self._db.get(Player, player_id)
        name = player.full_name if player is not None and player.full_name else ""
        label = " ".join(part for part in (jersey, name) if part) or f"player {player_id}"
        return f"{team_label} - {label}"

    # -- player identification ---------------------------------------

    def set_player_reference(
        self,
        event_id: int,
        team_side: TeamSide,
        *,
        jersey_number: int | None = None,
        unknown: bool = False,
        full_name: str | None = None,
    ) -> Event:
        """Resolve `team_side` ("home"/"away") plus a jersey number into
        this event's player-reference columns, per ticket 08's team-scoped
        jersey workflow. A jersey with no existing `GameRosterEntry` for
        that team in this game gets one created on the spot (and a new
        `Player`, via `resolve_or_create_roster_entry`) without blocking
        the tag. Pass `unknown=True` instead of a jersey number for an
        illegible/obstructed number."""
        event = self._get(event_id)
        reference = _PLAYER_REFERENCE_FIELDS.get(EventType(event.event_type))
        if reference is None:
            raise ValueError(f"{event.event_type.value} events have no player reference to set")
        id_column, unknown_column = reference
        team_id = self._team_id_for_side(team_side)

        team_field = _TEAM_FIELD[EventType(event.event_type)]
        setattr(event, team_field, team_id)

        if unknown:
            setattr(event, id_column, None)
            setattr(event, unknown_column, True)
        else:
            if jersey_number is None:
                raise ValueError("jersey_number is required unless unknown=True")
            entry = self.resolve_or_create_roster_entry(team_id, jersey_number, full_name=full_name)
            setattr(event, id_column, entry.player_id)
            setattr(event, unknown_column, False)

        self._db.commit()
        return event

    def resolve_or_create_roster_entry(
        self, team_id: int, jersey_number: int, *, full_name: str | None = None
    ) -> GameRosterEntry:
        """The existing `(game, team, jersey_number)` roster entry, or a
        brand-new `Player` + `GameRosterEntry` when none exists yet --
        tagging never requires a complete roster upfront (see CONTEXT.md's
        Game roster entry entry)."""
        stmt = select(GameRosterEntry).where(
            GameRosterEntry.game_id == self.game_id,
            GameRosterEntry.team_id == team_id,
            GameRosterEntry.jersey_number == jersey_number,
        )
        existing = self._db.scalars(stmt).one_or_none()
        if existing is not None:
            return existing

        player = Player(full_name=full_name)
        self._db.add(player)
        self._db.flush()  # assigns player.id, needed by the roster entry below

        entry = GameRosterEntry(
            game_id=self.game_id,
            player_id=player.id,
            team_id=team_id,
            jersey_number=jersey_number,
        )
        self._db.add(entry)
        self._db.commit()
        return entry

    def _team_id_for_side(self, team_side: TeamSide) -> int:
        if team_side == "home":
            return self.home_team_id
        if team_side == "away":
            return self.away_team_id
        raise ValueError(f"team_side must be 'home' or 'away', got {team_side!r}")

    # -- strength-state defaulting -------------------------------------

    def _infer_strength_state(self, up_to_timestamp: int) -> str | None:
        """The computed default from ticket 08: on-ice skater counts
        implied by `shift_change` events tagged so far, as of
        `up_to_timestamp`, formatted "{home}v{away}". A pure UI
        convenience -- never authoritative, always overridable -- so it
        deliberately returns `None` (leave the field to the tagger) once
        no shift data exists yet to infer anything from."""
        home = self._skater_count(self.home_team_id, up_to_timestamp)
        away = self._skater_count(self.away_team_id, up_to_timestamp)
        if home == 0 and away == 0:
            return None
        return f"{home}v{away}"

    def _skater_count(self, team_id: int, up_to_timestamp: int) -> int:
        stmt = (
            select(ShiftChange)
            .where(
                ShiftChange.game_id == self.game_id,
                ShiftChange.shift_team_id == team_id,
                ShiftChange.shift_player_id.is_not(None),
                ShiftChange.video_timestamp <= up_to_timestamp,
            )
            .order_by(ShiftChange.video_timestamp, ShiftChange.id)
        )
        on_ice: dict[int, bool] = {}
        for change in self._db.scalars(stmt):
            on_ice[change.shift_player_id] = bool(change.shift_on_ice)
        return sum(1 for is_on in on_ice.values() if is_on)
