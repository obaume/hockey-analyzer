"""SQLAlchemy ORM models for the core domain (see CONTEXT.md and
.scratch/hockey-analyzer-spec/spec.md). No GUI or video dependency.

`Event` is an abstract single-table-inheritance base: only the fields
every event carries live on it (see CONTEXT.md's Event entry), and each
of the seven subtypes (`PeriodStart`, `PeriodEnd`, `Stoppage`, `Faceoff`,
`ShotAttempt`, `Penalty`, `ShiftChange`) declares its own fields on its
own class. All subtypes still share one physical `events` table, so a
subtype's fields stay nullable at the database level (another subtype's
row simply doesn't use them); each subtype's CHECK constraints — attached
via `_add_constraints` right after its class, since SQLAlchemy only
builds `__table_args__` for a class that owns its table — narrow that to
"required for this subtype" (see ADR-0006 for why single-table
inheritance was chosen over joined-table).

Player-reference fields (shooter, assists, faceoff participants, the
penalized player, the shift participant) are each backed by a nullable
foreign key plus a companion `*_unknown` boolean, so a tagger can record an
explicit "unknown" instead of being blocked by an illegible jersey number
(see CONTEXT.md's Unknown player reference). A CHECK constraint enforces
that the two columns are never both/neither set for a required reference,
and never both set for an optional one (assist1/assist2, where "no value"
legitimately means "no assist").

Rink coordinates are stored raw; `zone` and `high_danger` are computed at
read time from `hockey_analyzer.domain.rink` and are never persisted. Game
clock and shift intervals are likewise never persisted here — they're
derived by later modules (TaggingSession/StatsEngine) from `Event` rows.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

from hockey_analyzer.domain.enums import (
    EventSource,
    EventType,
    Handedness,
    Position,
    RinkType,
    ShotOutcome,
    ShotType,
    UnitType,
)


class Base(DeclarativeBase):
    pass


def _enum_column(enum_cls):
    """A SQLAlchemy Enum type storing each member's `.value` (matching the
    vocabulary in CONTEXT.md, e.g. "shot_attempt") rather than its Python
    attribute name."""
    return SAEnum(enum_cls, values_callable=lambda members: [member.value for member in members])


_ConstraintFactory = Callable[[str], CheckConstraint]


def _required_reference_constraint(id_column: str, unknown_column: str, name: str) -> _ConstraintFactory:
    """For rows of the owning subtype: exactly one of (id set, unknown flag
    set) — the reference is always supplied, either as a known player or an
    explicit "unknown". Rows of other event types don't use this field at
    all, so they're left unconstrained. `for_event_type` (the owning
    subtype's `event_type` value) is supplied by `_add_constraints`, not
    here, so it's stated once per subtype rather than at every constraint."""

    def build(for_event_type: str) -> CheckConstraint:
        return CheckConstraint(
            f"event_type != '{for_event_type}' OR "
            f"(({id_column} IS NULL) AND ({unknown_column} = 1)) "
            f"OR (({id_column} IS NOT NULL) AND ({unknown_column} = 0))",
            name=name,
        )

    return build


def _optional_reference_constraint(id_column: str, unknown_column: str, name: str) -> CheckConstraint:
    """The reference may be entirely absent; if present, it's either a
    known player or an explicit "unknown", never both. Holds regardless of
    event type, since an unused field on another subtype's row is simply
    absent."""
    return CheckConstraint(
        f"NOT ({id_column} IS NOT NULL AND {unknown_column} = 1)",
        name=name,
    )


def _required_column_constraint(column: str, name: str) -> _ConstraintFactory:
    """For rows of the owning subtype, `column` must be set. Rows of other
    event types don't use this field, so they're left unconstrained. See
    `_required_reference_constraint` for why `for_event_type` is deferred
    to `_add_constraints`."""

    def build(for_event_type: str) -> CheckConstraint:
        return CheckConstraint(f"event_type != '{for_event_type}' OR {column} IS NOT NULL", name=name)

    return build


def _add_constraints(model: type["Event"], *constraints: CheckConstraint | _ConstraintFactory) -> None:
    """Attach an `Event` subtype's CHECK constraints to the shared
    `events` table. Declarative only builds `__table_args__` for a class
    that owns its table, which a single-table-inheritance subtype
    doesn't — so constraints are appended to the inherited table directly,
    right after the subtype's class body, to keep them next to the fields
    they govern. A `_ConstraintFactory` (from `_required_reference_constraint`
    /`_required_column_constraint`) is resolved here using the subtype's own
    `polymorphic_identity`, so `for_event_type` is stated once per subtype
    rather than repeated at each constraint call site."""
    for_event_type = model.__mapper__.polymorphic_identity.value
    for constraint in constraints:
        resolved = constraint if isinstance(constraint, CheckConstraint) else constraint(for_event_type)
        model.__table__.append_constraint(resolved)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    league_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    is_user_team: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    roster_entries: Mapped[list["GameRosterEntry"]] = relationship(back_populates="team")
    unit_assignments: Mapped[list["GameUnitAssignment"]] = relationship(back_populates="team")


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    handedness: Mapped[Handedness | None] = mapped_column(_enum_column(Handedness), nullable=True)
    position: Mapped[Position | None] = mapped_column(_enum_column(Position), nullable=True)

    roster_entries: Mapped[list["GameRosterEntry"]] = relationship(back_populates="player")
    unit_assignments: Mapped[list["GameUnitAssignment"]] = relationship(back_populates="player")


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    date: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    venue: Mapped[str | None] = mapped_column(String, nullable=True)
    # Which physical rink-dimension standard this game's location-bearing
    # events (faceoff/shot_attempt) derive zone/high_danger against (see
    # domain/rink.py and CONTEXT.md's Rink type entry). Set once here and
    # never edited afterward -- there is no setter for it anywhere in this
    # codebase, deliberately (see ADR-0008).
    rink_type: Mapped[RinkType] = mapped_column(_enum_column(RinkType), nullable=False, default=RinkType.IIHF)
    opponent_shifts_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # List of {"home": int, "away": int} dicts, one per period. Context only
    # (see CONTEXT.md's Game entry) — no stats-engine consumer.
    period_scores: Mapped[list | None] = mapped_column(JSON, nullable=True)

    roster_entries: Mapped[list["GameRosterEntry"]] = relationship(back_populates="game")
    unit_assignments: Mapped[list["GameUnitAssignment"]] = relationship(back_populates="game")
    events: Mapped[list["Event"]] = relationship(back_populates="game")


class GameRosterEntry(Base):
    __tablename__ = "game_roster_entries"
    __table_args__ = (
        UniqueConstraint("game_id", "team_id", "jersey_number", name="uq_roster_jersey_per_game_team"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    jersey_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Game-of-the-day position from a league-API roster import only; never
    # written to Player.position (see CONTEXT.md's Game roster entry).
    position: Mapped[Position | None] = mapped_column(_enum_column(Position), nullable=True)

    game: Mapped["Game"] = relationship(back_populates="roster_entries")
    player: Mapped["Player"] = relationship(back_populates="roster_entries")
    team: Mapped["Team"] = relationship(back_populates="roster_entries")


class GameUnitAssignment(Base):
    __tablename__ = "game_unit_assignments"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id", "unit_type", name="uq_unit_per_game_player_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    unit_type: Mapped[UnitType] = mapped_column(_enum_column(UnitType), nullable=False)
    unit_number: Mapped[int] = mapped_column(Integer, nullable=False)

    game: Mapped["Game"] = relationship(back_populates="unit_assignments")
    player: Mapped["Player"] = relationship(back_populates="unit_assignments")
    team: Mapped["Team"] = relationship(back_populates="unit_assignments")


class Event(Base):
    """Abstract single-table-inheritance base: only the fields every event
    carries regardless of subtype live here (see CONTEXT.md's Event entry).
    Subtype-specific fields and constraints live on the seven classes below
    (see module docstring and ADR-0006)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    event_type: Mapped[EventType] = mapped_column(_enum_column(EventType), nullable=False)
    video_timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[EventSource] = mapped_column(_enum_column(EventSource), nullable=False, default=EventSource.MANUAL)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Explicit per-event value (e.g. "5v5", "PP", "4v4", "EN") — never
    # derived from the penalty timeline (see CONTEXT.md's Strength state).
    strength_state: Mapped[str | None] = mapped_column(String, nullable=True)

    game: Mapped["Game"] = relationship(back_populates="events")

    __mapper_args__ = {
        "polymorphic_on": event_type,
        "polymorphic_abstract": True,
    }


class _PeriodNumberMixin:
    """Shared by `PeriodStart`/`PeriodEnd`: single-table inheritance means
    both classes map to the same `events` table, so this must be one
    physical column, not two independently-declared ones."""

    @declared_attr
    def period_number(cls) -> Mapped[int | None]:
        return mapped_column(Integer, nullable=True, use_existing_column=True)


class PeriodStart(_PeriodNumberMixin, Event):
    __mapper_args__ = {"polymorphic_identity": EventType.PERIOD_START}


class PeriodEnd(_PeriodNumberMixin, Event):
    __mapper_args__ = {"polymorphic_identity": EventType.PERIOD_END}


class Stoppage(Event):
    __mapper_args__ = {"polymorphic_identity": EventType.STOPPAGE}


class Faceoff(Event):
    """Raw coordinates only; `zone` is derived, never stored."""

    __mapper_args__ = {"polymorphic_identity": EventType.FACEOFF}

    faceoff_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    faceoff_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    faceoff_team_a_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    faceoff_participant_a_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    faceoff_participant_a_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    faceoff_team_b_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    faceoff_participant_b_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    faceoff_participant_b_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    faceoff_winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)


_add_constraints(
    Faceoff,
    _required_reference_constraint(
        "faceoff_participant_a_id", "faceoff_participant_a_unknown", "ck_faceoff_participant_a_ref"
    ),
    _required_reference_constraint(
        "faceoff_participant_b_id", "faceoff_participant_b_unknown", "ck_faceoff_participant_b_ref"
    ),
)


class ShotAttempt(Event):
    """Raw coordinates only; `zone`/`high_danger` are derived, never
    stored."""

    __mapper_args__ = {"polymorphic_identity": EventType.SHOT_ATTEMPT}

    shot_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    shot_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    shot_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    shot_outcome: Mapped[ShotOutcome | None] = mapped_column(_enum_column(ShotOutcome), nullable=True)
    shot_type: Mapped[ShotType | None] = mapped_column(_enum_column(ShotType), nullable=True)
    shot_rush: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shot_rebound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shot_screened: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shot_one_timer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Populated only for goal/saved outcomes; null until the xG model is
    # calibrated (out of scope here — see CONTEXT.md's xG entry).
    shot_xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    shooter_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    shooter_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assist1_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    assist1_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assist2_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    assist2_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


_add_constraints(
    ShotAttempt,
    _required_reference_constraint("shooter_id", "shooter_unknown", "ck_shooter_ref"),
    _optional_reference_constraint("assist1_id", "assist1_unknown", "ck_assist1_ref"),
    _optional_reference_constraint("assist2_id", "assist2_unknown", "ck_assist2_ref"),
    _required_column_constraint("shot_type", "ck_shot_type_required"),
    _required_column_constraint("shot_outcome", "ck_shot_outcome_required"),
    CheckConstraint("shot_xg IS NULL OR (shot_xg >= 0 AND shot_xg <= 1)", name="ck_shot_xg_range"),
    CheckConstraint("shot_xg IS NULL OR shot_outcome IN ('goal', 'saved')", name="ck_shot_xg_only_for_shots_on_goal"),
)


class Penalty(Event):
    __mapper_args__ = {"polymorphic_identity": EventType.PENALTY}

    penalty_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    penalty_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    penalty_player_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    penalty_duration_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    penalty_infraction: Mapped[str | None] = mapped_column(String, nullable=True)


_add_constraints(
    Penalty,
    _required_reference_constraint("penalty_player_id", "penalty_player_unknown", "ck_penalty_player_ref"),
)


class ShiftChange(Event):
    __mapper_args__ = {"polymorphic_identity": EventType.SHIFT_CHANGE}

    shift_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    shift_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    shift_player_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shift_on_ice: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


_add_constraints(
    ShiftChange,
    _required_reference_constraint("shift_player_id", "shift_player_unknown", "ck_shift_player_ref"),
)
