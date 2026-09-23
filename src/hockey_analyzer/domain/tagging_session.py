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

from typing import NamedTuple

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from hockey_analyzer.domain.enums import EventType, RinkType, ShotOutcome, ShotType, Side
from hockey_analyzer.domain.models import (
    Event,
    Faceoff,
    Game,
    GameRosterEntry,
    Penalty,
    PeriodEnd,
    PeriodStart,
    Player,
    ShiftChange,
    ShotAttempt,
    Stoppage,
    Team,
)

_MODEL_BY_TYPE: dict[EventType, type[Event]] = {
    EventType.PERIOD_START: PeriodStart,
    EventType.PERIOD_END: PeriodEnd,
    EventType.STOPPAGE: Stoppage,
    EventType.FACEOFF: Faceoff,
    EventType.SHOT_ATTEMPT: ShotAttempt,
    EventType.PENALTY: Penalty,
    EventType.SHIFT_CHANGE: ShiftChange,
}


class _ReferenceSpec(NamedTuple):
    """One player reference on an event subtype: its id/unknown column
    pair, plus the team column it resolves onto -- `None` for a reference
    with no team column of its own (assist1/assist2, which record a
    player but not an independently-tracked team; see models.py)."""

    id_column: str
    unknown_column: str
    team_column: str | None


# Every player reference an in-scope subtype carries, keyed by a
# reference name the caller passes to `set_player_reference`. A subtype
# with exactly one reference (penalty/shift_change) lets that name default
# implicitly; faceoff/shot_attempt, which carry more than one, require it.
_REFERENCE_SPECS: dict[EventType, dict[str, _ReferenceSpec]] = {
    EventType.PENALTY: {
        "player": _ReferenceSpec("penalty_player_id", "penalty_player_unknown", "penalty_team_id"),
    },
    EventType.SHIFT_CHANGE: {
        "player": _ReferenceSpec("shift_player_id", "shift_player_unknown", "shift_team_id"),
    },
    EventType.FACEOFF: {
        "participant_a": _ReferenceSpec(
            "faceoff_participant_a_id", "faceoff_participant_a_unknown", "faceoff_team_a_id"
        ),
        "participant_b": _ReferenceSpec(
            "faceoff_participant_b_id", "faceoff_participant_b_unknown", "faceoff_team_b_id"
        ),
    },
    EventType.SHOT_ATTEMPT: {
        "shooter": _ReferenceSpec("shooter_id", "shooter_unknown", "shot_team_id"),
        "assist1": _ReferenceSpec("assist1_id", "assist1_unknown", None),
        "assist2": _ReferenceSpec("assist2_id", "assist2_unknown", None),
    },
}

# References that must be defaulted to explicit "unknown" the instant a
# stub event is created, since models.py's CHECK constraint demands
# exactly one of (id, unknown) be set at all times for them. Assist
# references are excluded -- their constraint allows both columns null,
# meaning "no assist", so a fresh stub leaves them alone.
_REQUIRED_REFERENCES: dict[EventType, tuple[str, ...]] = {
    EventType.PENALTY: ("player",),
    EventType.SHIFT_CHANGE: ("player",),
    EventType.FACEOFF: ("participant_a", "participant_b"),
    EventType.SHOT_ATTEMPT: ("shooter",),
}

# Backed by the same closed vocabulary GameSetupService persists
# Game.home_team_id/away_team_id against (see enums.Side) -- kept under
# this file's existing public name since tagging_panel.py already
# imports it as such.
TeamSide = Side

# Public: the widget layer needs these to decide which event types show a
# player-reference field (and, for faceoff/shot_attempt, which reference
# names to show) without redeclaring the mapping independently.
EVENT_TYPE_REFERENCE_NAMES: dict[EventType, tuple[str, ...]] = {
    event_type: tuple(specs) for event_type, specs in _REFERENCE_SPECS.items()
}
EVENT_TYPES_WITH_PLAYER_REFERENCE = frozenset(EVENT_TYPE_REFERENCE_NAMES)


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

    @property
    def rink_type(self) -> RinkType:
        """The tagged game's rink standard (see CONTEXT.md's Rink type
        entry) -- read-only, matching `Game.rink_type` being immutable
        after creation (ADR-0008). The UI layer reads this to pick which
        rink template to render, without a separate DB query of its own."""
        return self._db.get(Game, self.game_id).rink_type

    # -- event CRUD -------------------------------------------------

    def log_event(
        self,
        event_type: EventType,
        video_timestamp: int,
        *,
        strength_state: str | None = None,
        shot_outcome: ShotOutcome | None = None,
        shot_type: ShotType | None = None,
    ) -> Event:
        """Instantly capture an event's timestamp -- see module docstring.
        `strength_state` defaults to the computed on-ice-count guess
        (`_infer_strength_state`) when omitted, but is always explicitly
        overridable, including right here at capture time.

        `shot_outcome`/`shot_type` are required for `shot_attempt` only:
        unlike every other in-scope field, models.py's CHECK constraints
        make them mandatory on every row of that subtype with no "unknown"
        stand-in for outcome, so a bare stub can't be committed without
        them -- the tagger supplies both the moment the shot is logged,
        alongside the rink-coordinate click (see ticket 16)."""
        model = _MODEL_BY_TYPE[event_type]
        fields: dict[str, object] = {}
        specs = _REFERENCE_SPECS.get(event_type, {})
        for reference_name in _REQUIRED_REFERENCES.get(event_type, ()):
            fields[specs[reference_name].unknown_column] = True

        if event_type is EventType.SHOT_ATTEMPT:
            if shot_outcome is None or shot_type is None:
                raise ValueError("shot_attempt requires shot_outcome and shot_type")
            fields["shot_outcome"] = shot_outcome
            fields["shot_type"] = shot_type

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
        if event_type is EventType.FACEOFF:
            participant_a = self._describe_player_reference(
                event.faceoff_team_a_id, event.faceoff_participant_a_id, event.faceoff_participant_a_unknown
            )
            participant_b = self._describe_player_reference(
                event.faceoff_team_b_id, event.faceoff_participant_b_id, event.faceoff_participant_b_unknown
            )
            return f"{participant_a} vs {participant_b}"
        if event_type is EventType.SHOT_ATTEMPT:
            shooter = self._describe_player_reference(event.shot_team_id, event.shooter_id, event.shooter_unknown)
            outcome = event.shot_outcome.value if event.shot_outcome is not None else "outcome not set"
            return f"{shooter} - {outcome}"
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
        reference: str | None = None,
        jersey_number: int | None = None,
        unknown: bool = False,
        full_name: str | None = None,
    ) -> Event:
        """Resolve `team_side` ("home"/"away") plus a jersey number into
        one of this event's player-reference columns, per ticket 08's
        team-scoped jersey workflow. A jersey with no existing
        `GameRosterEntry` for that team in this game gets one created on
        the spot (and a new `Player`, via `resolve_or_create_roster_entry`)
        without blocking the tag. Pass `unknown=True` instead of a jersey
        number for an illegible/obstructed number.

        `reference` picks which of the event's player references to set
        (e.g. "shooter"/"assist1"/"assist2" on a `shot_attempt`,
        "participant_a"/"participant_b" on a `faceoff`) and may be omitted
        for a subtype that carries only one, such as `penalty`/
        `shift_change`."""
        event = self._get(event_id)
        event_type = EventType(event.event_type)
        specs = _REFERENCE_SPECS.get(event_type)
        if not specs:
            raise ValueError(f"{event.event_type.value} events have no player reference to set")
        if reference is None:
            if len(specs) != 1:
                raise ValueError(
                    f"{event.event_type.value} events carry more than one player reference "
                    f"({', '.join(specs)}); pass reference=<name>"
                )
            reference = next(iter(specs))
        spec = specs.get(reference)
        if spec is None:
            raise ValueError(f"{event.event_type.value} events have no {reference!r} reference")

        team_id = self._team_id_for_side(team_side)
        if spec.team_column is not None:
            setattr(event, spec.team_column, team_id)

        if unknown:
            setattr(event, spec.id_column, None)
            setattr(event, spec.unknown_column, True)
        else:
            if jersey_number is None:
                raise ValueError("jersey_number is required unless unknown=True")
            entry = self.resolve_or_create_roster_entry(team_id, jersey_number, full_name=full_name)
            setattr(event, spec.id_column, entry.player_id)
            setattr(event, spec.unknown_column, False)

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
