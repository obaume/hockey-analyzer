from __future__ import annotations

from hockey_analyzer.domain.enums import RinkType
from hockey_analyzer.domain.rink import high_danger, zone

# -- zone: NHL geometry (blue line +/-25.0) ---------------------------------


def test_zone_offensive_when_past_attacked_blue_line_nhl():
    assert zone(x=40.0, attacking_direction=1, rink_type=RinkType.NHL) == "offensive"


def test_zone_defensive_when_behind_own_blue_line_nhl():
    assert zone(x=-40.0, attacking_direction=1, rink_type=RinkType.NHL) == "defensive"


def test_zone_neutral_between_blue_lines_nhl():
    assert zone(x=0.0, attacking_direction=1, rink_type=RinkType.NHL) == "neutral"


def test_zone_flips_with_attacking_direction_nhl():
    assert zone(x=40.0, attacking_direction=-1, rink_type=RinkType.NHL) == "defensive"
    assert zone(x=-40.0, attacking_direction=-1, rink_type=RinkType.NHL) == "offensive"


# -- zone: IIHF geometry (blue line +/-24.6) --------------------------------


def test_zone_offensive_when_past_attacked_blue_line_iihf():
    assert zone(x=30.0, attacking_direction=1, rink_type=RinkType.IIHF) == "offensive"


def test_zone_defensive_when_behind_own_blue_line_iihf():
    assert zone(x=-30.0, attacking_direction=1, rink_type=RinkType.IIHF) == "defensive"


def test_zone_neutral_between_blue_lines_iihf():
    assert zone(x=0.0, attacking_direction=1, rink_type=RinkType.IIHF) == "neutral"


def test_zone_iihf_and_nhl_disagree_in_the_gap_between_their_blue_lines():
    # x=24.8 is inside NHL's neutral zone (blue line at 25.0) but past
    # IIHF's blue line (24.6) -- the two standards must be genuinely
    # independent, not just aliases of the same numbers.
    assert zone(x=24.8, attacking_direction=1, rink_type=RinkType.NHL) == "neutral"
    assert zone(x=24.8, attacking_direction=1, rink_type=RinkType.IIHF) == "offensive"


# -- high_danger: NHL geometry (goal line +/-89.0) --------------------------


def test_high_danger_true_right_in_front_of_net_nhl():
    assert high_danger(x=85.0, y=0.0, rink_type=RinkType.NHL) is True


def test_high_danger_false_from_center_ice_nhl():
    assert high_danger(x=0.0, y=0.0, rink_type=RinkType.NHL) is False


def test_high_danger_false_from_a_bad_angle_along_the_boards_nhl():
    assert high_danger(x=88.0, y=40.0, rink_type=RinkType.NHL) is False


def test_high_danger_considers_the_nearer_net_nhl():
    assert high_danger(x=-85.0, y=0.0, rink_type=RinkType.NHL) is True


# -- high_danger: IIHF geometry (goal line +/-85.4) -------------------------


def test_high_danger_true_right_in_front_of_net_iihf():
    assert high_danger(x=81.0, y=0.0, rink_type=RinkType.IIHF) is True


def test_high_danger_false_from_center_ice_iihf():
    assert high_danger(x=0.0, y=0.0, rink_type=RinkType.IIHF) is False


def test_high_danger_considers_the_nearer_net_iihf():
    assert high_danger(x=-81.0, y=0.0, rink_type=RinkType.IIHF) is True


def test_high_danger_radius_is_the_same_absolute_distance_regardless_of_rink_type():
    # A point exactly HIGH_DANGER_RADIUS (20ft) from each standard's own
    # goal line is high-danger under both -- the heuristic doesn't scale
    # with the wider IIHF rink (see ADR-0008).
    assert high_danger(x=89.0 - 20.0, y=0.0, rink_type=RinkType.NHL) is True
    assert high_danger(x=85.4 - 20.0, y=0.0, rink_type=RinkType.IIHF) is True
