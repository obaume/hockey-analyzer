from __future__ import annotations

import pytest

from hockey_analyzer.domain.video_timestamp import format_video_timestamp, parse_video_timestamp

# -- format_video_timestamp ---------------------------------------------------


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "0:00:00"),
        (45_000, "0:00:45"),
        (754_000, "0:12:34"),
        (3_723_000, "1:02:03"),
        (7_500_000, "2:05:00"),
        (36_000_000, "10:00:00"),
    ],
)
def test_format_always_shows_hours(ms, expected):
    assert format_video_timestamp(ms) == expected


def test_format_truncates_sub_second_part():
    assert format_video_timestamp(754_999) == "0:12:34"


# -- parse_video_timestamp: accepted input ------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("45", 45_000),
        ("12:34", 754_000),
        ("1:2:3", 3_723_000),
        ("5:7", 307_000),
        ("0:12:34", 754_000),
        ("00:05", 5_000),
        ("0", 0),
        ("25:00:00", 90_000_000),
        ("  12:34 ", 754_000),
    ],
)
def test_parse_resolves_fields_right_to_left(text, expected):
    assert parse_video_timestamp(text) == expected


def test_parse_round_trips_formatted_value():
    assert parse_video_timestamp(format_video_timestamp(3_723_000)) == 3_723_000


# -- parse_video_timestamp: cancelled input -----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "12:",
        ":34",
        "1::3",
        "90",
        "1:75",
        "60:00",
        "1:60:00",
        "-1:00",
        "+1:00",
        "1:02.5",
        "1:2:3:4",
        "abc",
        "1 2",
    ],
)
def test_parse_returns_none_on_bad_input(text):
    assert parse_video_timestamp(text) is None
