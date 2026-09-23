"""Closed-vocabulary enums used by the domain model (see CONTEXT.md)."""

from __future__ import annotations

import enum


class Handedness(str, enum.Enum):
    LEFT = "left"
    RIGHT = "right"


class Position(str, enum.Enum):
    CENTER = "C"
    LEFT_WING = "LW"
    RIGHT_WING = "RW"
    DEFENSE = "D"
    GOALIE = "G"


class EventType(str, enum.Enum):
    PERIOD_START = "period_start"
    PERIOD_END = "period_end"
    STOPPAGE = "stoppage"
    FACEOFF = "faceoff"
    SHOT_ATTEMPT = "shot_attempt"
    PENALTY = "penalty"
    SHIFT_CHANGE = "shift_change"


class EventSource(str, enum.Enum):
    MANUAL = "manual"
    VISION = "vision"


class ShotOutcome(str, enum.Enum):
    GOAL = "goal"
    SAVED = "saved"
    MISSED = "missed"
    BLOCKED = "blocked"


class ShotType(str, enum.Enum):
    WRIST = "wrist"
    SLAP = "slap"
    SNAP = "snap"
    BACKHAND = "backhand"
    TIP = "tip"
    WRAP_AROUND = "wrap-around"
    UNKNOWN = "unknown"


class UnitType(str, enum.Enum):
    FORWARD_LINE = "forward-line"
    DEFENSE_PAIR = "defense-pair"
    POWER_PLAY = "power-play"
    PENALTY_KILL = "penalty-kill"


class RinkType(str, enum.Enum):
    IIHF = "iihf"
    NHL = "nhl"


class Side(str, enum.Enum):
    """Which side of a Game a Team is on -- shared by TaggingSession
    (event-level team references) and GameSetupService (persisting
    Game.home_team_id/away_team_id), so there is exactly one closed
    vocabulary for this concept rather than one per module."""

    HOME = "home"
    AWAY = "away"
