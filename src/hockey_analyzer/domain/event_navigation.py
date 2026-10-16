"""Event navigation (#88): where the video goes when the tagger skims a
game event by event. Each distinct `video_timestamp` among the game's
events is one stop (a place the skim stops at -- not a **Stoppage**): a
line change's shift changes at the same instant are one stop, events
500 ms apart are two. Only identical timestamps group; every event type
counts, unconfirmed vision suggestions included.
"""

from __future__ import annotations

import enum
from bisect import bisect_left, bisect_right
from collections.abc import Iterable

# A seek can report a position a few ms short of where it was asked to
# land; a stop this close ahead of the playhead counts as the one it's
# on, so next moves past it rather than landing on it again. Well under
# a frame, so a stop one frame ahead is still the next one.
SEEK_TOLERANCE_MS = 10

# While playing, the playhead has already moved on by the time a key is
# pressed. A stop less than this far behind it is the one just landed on
# (or just played past), so previous skips it for the one before.
PREVIOUS_GRACE_MS = 1000


class Direction(enum.Enum):
    NEXT = enum.auto()
    PREVIOUS = enum.auto()
    FIRST = enum.auto()
    LAST = enum.auto()


def event_stop(
    timestamps: Iterable[int],
    playhead_ms: int,
    direction: Direction,
    *,
    playing: bool = False,
) -> int | None:
    """The stop to seek to from `playhead_ms`, or None when there's
    nowhere to go: past the last stop, before the first, or no events.
    Never wraps around."""
    stops = sorted(set(timestamps))
    if not stops:
        return None
    if direction is Direction.FIRST:
        return stops[0]
    if direction is Direction.LAST:
        return stops[-1]
    if direction is Direction.NEXT:
        ahead = bisect_right(stops, playhead_ms + SEEK_TOLERANCE_MS)
        return stops[ahead] if ahead < len(stops) else None
    if playing:
        behind = stops[: bisect_right(stops, playhead_ms + SEEK_TOLERANCE_MS)]
        if behind and playhead_ms - behind[-1] < PREVIOUS_GRACE_MS:
            behind.pop()
    else:
        behind = stops[: bisect_left(stops, playhead_ms)]
    return behind[-1] if behind else None
