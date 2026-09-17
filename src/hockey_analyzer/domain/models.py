"""SQLAlchemy ORM models for the core domain (see CONTEXT.md and
.scratch/hockey-analyzer-spec/spec.md). No GUI or video dependency.

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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from hockey_analyzer.domain.enums import (
    EventSource,
    EventType,
    Handedness,
    Position,
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


def _required_reference_constraint(
    id_column: str, unknown_column: str, name: str, *, for_event_type: str
) -> CheckConstraint:
    """For rows of `for_event_type`: exactly one of (id set, unknown flag
    set) — the reference is always supplied, either as a known player or an
    explicit "unknown". Rows of other event types don't use this field at
    all, so they're left unconstrained."""
    return CheckConstraint(
        f"event_type != '{for_event_type}' OR "
        f"(({id_column} IS NULL) AND ({unknown_column} = 1)) "
        f"OR (({id_column} IS NOT NULL) AND ({unknown_column} = 0))",
        name=name,
    )


def _optional_reference_constraint(id_column: str, unknown_column: str, name: str) -> CheckConstraint:
    """The reference may be entirely absent; if present, it's either a
    known player or an explicit "unknown", never both. Holds regardless of
    event type, since an unused field on another subtype's row is simply
    absent."""
    return CheckConstraint(
        f"NOT ({id_column} IS NOT NULL AND {unknown_column} = 1)",
        name=name,
    )


def _required_column_constraint(column: str, name: str, *, for_event_type: str) -> CheckConstraint:
    """For rows of `for_event_type`, `column` must be set. Rows of other
    event types don't use this field, so they're left unconstrained."""
    return CheckConstraint(f"event_type != '{for_event_type}' OR {column} IS NOT NULL", name=name)


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
    """Single table covering all seven subtypes; each subtype only uses its
    own slice of the nullable columns below (see module docstring and
    CONTEXT.md's Event entry for the discriminator/rationale)."""

    __tablename__ = "events"
    __table_args__ = (
        _required_reference_constraint(
            "shift_player_id", "shift_player_unknown", "ck_shift_player_ref", for_event_type=EventType.SHIFT_CHANGE.value
        ),
        _required_reference_constraint(
            "faceoff_participant_a_id",
            "faceoff_participant_a_unknown",
            "ck_faceoff_participant_a_ref",
            for_event_type=EventType.FACEOFF.value,
        ),
        _required_reference_constraint(
            "faceoff_participant_b_id",
            "faceoff_participant_b_unknown",
            "ck_faceoff_participant_b_ref",
            for_event_type=EventType.FACEOFF.value,
        ),
        _required_reference_constraint(
            "shooter_id", "shooter_unknown", "ck_shooter_ref", for_event_type=EventType.SHOT_ATTEMPT.value
        ),
        _optional_reference_constraint("assist1_id", "assist1_unknown", "ck_assist1_ref"),
        _optional_reference_constraint("assist2_id", "assist2_unknown", "ck_assist2_ref"),
        _required_reference_constraint(
            "penalty_player_id",
            "penalty_player_unknown",
            "ck_penalty_player_ref",
            for_event_type=EventType.PENALTY.value,
        ),
        _required_column_constraint("shot_type", "ck_shot_type_required", for_event_type=EventType.SHOT_ATTEMPT.value),
        _required_column_constraint(
            "shot_outcome", "ck_shot_outcome_required", for_event_type=EventType.SHOT_ATTEMPT.value
        ),
        CheckConstraint("shot_xg IS NULL OR (shot_xg >= 0 AND shot_xg <= 1)", name="ck_shot_xg_range"),
        CheckConstraint(
            "shot_xg IS NULL OR shot_outcome IN ('goal', 'saved')", name="ck_shot_xg_only_for_shots_on_goal"
        ),
    )

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

    # -- period_start / period_end --
    period_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # -- faceoff (raw coordinates only; zone is derived, never stored) --
    faceoff_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    faceoff_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    faceoff_team_a_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    faceoff_participant_a_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    faceoff_participant_a_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    faceoff_team_b_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    faceoff_participant_b_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    faceoff_participant_b_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    faceoff_winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)

    # -- shot_attempt (raw coordinates only; zone/high_danger derived) --
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

    # -- penalty --
    penalty_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    penalty_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    penalty_player_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    penalty_duration_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    penalty_infraction: Mapped[str | None] = mapped_column(String, nullable=True)

    # -- shift_change --
    shift_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    shift_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    shift_player_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shift_on_ice: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
