from __future__ import annotations

from hockey_analyzer.domain.rink import high_danger, zone


def test_zone_offensive_when_past_attacked_blue_line():
    assert zone(x=40.0, attacking_direction=1) == "offensive"


def test_zone_defensive_when_behind_own_blue_line():
    assert zone(x=-40.0, attacking_direction=1) == "defensive"


def test_zone_neutral_between_blue_lines():
    assert zone(x=0.0, attacking_direction=1) == "neutral"


def test_zone_flips_with_attacking_direction():
    assert zone(x=40.0, attacking_direction=-1) == "defensive"
    assert zone(x=-40.0, attacking_direction=-1) == "offensive"


def test_high_danger_true_right_in_front_of_net():
    assert high_danger(x=85.0, y=0.0) is True


def test_high_danger_false_from_center_ice():
    assert high_danger(x=0.0, y=0.0) is False


def test_high_danger_false_from_a_bad_angle_along_the_boards():
    assert high_danger(x=88.0, y=40.0) is False


def test_high_danger_considers_the_nearer_net():
    assert high_danger(x=-85.0, y=0.0) is True
