"""Text form of a video timestamp (see CONTEXT.md's Video timestamp entry):
an offset into the footage in ms, shown as `H:MM:SS`.

Parsing reads the typed fields right to left -- the last field is seconds,
then minutes, then hours -- so partial input like `"45"` or `"12:34"`
resolves to an offset. Anything ambiguous or malformed (an empty field,
minutes/seconds outside 0-59, non-digits, more than three fields) yields
`None` so the caller can discard the edit rather than guess.
"""

from __future__ import annotations

import re

_FIELD = re.compile(r"[0-9]+")


def format_video_timestamp(video_timestamp_ms: int) -> str:
    total_seconds = video_timestamp_ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def parse_video_timestamp(text: str) -> int | None:
    fields = text.strip().split(":")
    if len(fields) > 3 or not all(_FIELD.fullmatch(field) for field in fields):
        return None
    seconds, minutes, hours = (int(field) for field in [*reversed(fields), "0", "0"][:3])
    if minutes > 59 or seconds > 59:
        return None
    return ((hours * 60 + minutes) * 60 + seconds) * 1000
