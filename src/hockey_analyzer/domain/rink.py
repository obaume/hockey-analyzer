"""Fixed rink geometry used to derive `zone` and `high_danger` from stored
(x, y) coordinates at read time. Neither value is ever persisted (see
CONTEXT.md's Rink coordinates / Danger zone entries).

Coordinate system: origin at center ice, x along the rink's long axis in
feet, y across its width in feet -- but the actual extent, blue-line, and
goal-line positions depend on which of the two supported standards a game
was tagged under (see CONTEXT.md's Rink type entry and ADR-0008): there is
no single implicit rink shape, so every function here takes an explicit
`RinkType` rather than defaulting to one.
"""

from __future__ import annotations

import math
from typing import Literal, NamedTuple

from hockey_analyzer.domain.enums import RinkType

Zone = Literal["defensive", "neutral", "offensive"]


class RinkDimensions(NamedTuple):
    blue_line_x: float
    goal_line_x: float


# Feet from center ice. IIHF: 15.0m/49.2ft neutral zone (blue line at
# +/-7.5m/24.6ft) and 4.0m/13.1ft goal-line depth from the boards (half-
# length 98.5ft - 13.1ft = 85.4ft) -- both match hockey_rink's IIHFRink
# almost exactly. NHL: the standard 200x85ft rink (blue lines +/-25ft,
# goal lines +/-89ft).
RINK_DIMENSIONS: dict[RinkType, RinkDimensions] = {
    RinkType.IIHF: RinkDimensions(blue_line_x=24.6, goal_line_x=85.4),
    RinkType.NHL: RinkDimensions(blue_line_x=25.0, goal_line_x=89.0),
}

# Simplified "home plate" high-danger area: within this radius of the net
# mouth, and not so wide that it spills past the top of the slot. A fixed
# shot-proximity heuristic, deliberately not derived from or scaled to a
# rink's overall width -- IIHF's wider rink doesn't make a shot from 20ft
# out any more dangerous (see ADR-0008).
HIGH_DANGER_RADIUS = 20.0
HIGH_DANGER_HALF_WIDTH = 22.0


def zone(x: float, attacking_direction: Literal[1, -1], rink_type: RinkType) -> Zone:
    """Zone relative to a team attacking toward `attacking_direction`
    (+1 = positive x, -1 = negative x), on `rink_type`'s geometry."""
    blue_line_x = RINK_DIMENSIONS[rink_type].blue_line_x
    signed_x = x * attacking_direction
    if signed_x > blue_line_x:
        return "offensive"
    if signed_x < -blue_line_x:
        return "defensive"
    return "neutral"


def high_danger(x: float, y: float, rink_type: RinkType) -> bool:
    """Whether (x, y) falls in the high-danger area in front of whichever
    net is nearer, on `rink_type`'s geometry."""
    goal_line_x = RINK_DIMENSIONS[rink_type].goal_line_x
    nearest_net_x = goal_line_x if x >= 0 else -goal_line_x
    distance = math.hypot(x - nearest_net_x, y)
    return distance <= HIGH_DANGER_RADIUS and abs(y) <= HIGH_DANGER_HALF_WIDTH
