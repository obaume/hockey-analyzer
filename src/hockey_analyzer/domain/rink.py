"""Fixed rink geometry used to derive `zone` and `high_danger` from stored
(x, y) coordinates at read time. Neither value is ever persisted (see
CONTEXT.md's Rink coordinates / Danger zone entries).

Coordinate system: origin at center ice, x along the rink's long axis in
feet (+/-100), y across its width (+/-42.5). Blue lines sit at x = +/-25;
goal lines (and the nets in front of them) sit at x = +/-89, matching a
standard 200x85 ft rink.
"""

from __future__ import annotations

import math
from typing import Literal

Zone = Literal["defensive", "neutral", "offensive"]

BLUE_LINE_X = 25.0
GOAL_LINE_X = 89.0

# Simplified "home plate" high-danger area: within this radius of the net
# mouth, and not so wide that it spills past the top of the slot.
HIGH_DANGER_RADIUS = 20.0
HIGH_DANGER_HALF_WIDTH = 22.0


def zone(x: float, attacking_direction: Literal[1, -1]) -> Zone:
    """Zone relative to a team attacking toward `attacking_direction`
    (+1 = positive x, -1 = negative x)."""
    signed_x = x * attacking_direction
    if signed_x > BLUE_LINE_X:
        return "offensive"
    if signed_x < -BLUE_LINE_X:
        return "defensive"
    return "neutral"


def high_danger(x: float, y: float) -> bool:
    """Whether (x, y) falls in the high-danger area in front of whichever
    net is nearer."""
    nearest_net_x = GOAL_LINE_X if x >= 0 else -GOAL_LINE_X
    distance = math.hypot(x - nearest_net_x, y)
    return distance <= HIGH_DANGER_RADIUS and abs(y) <= HIGH_DANGER_HALF_WIDTH
