from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from hockey_analyzer.domain.enums import Side, UnitType
from hockey_analyzer.domain.tagging_session import Unit
from hockey_analyzer.ui.line_change_dialog import LineChangeDialog

_LINE_1 = Unit(Side.HOME, UnitType.FORWARD_LINE, 1, (1, 2, 3))
_PP_1 = Unit(Side.HOME, UnitType.POWER_PLAY, 1, (1, 4))
_ROSTER = {"home": [("#14 Kim", 14)], "away": [("#7", 7), ("#12 Diaz", 12)]}


def _make_dialog(qtbot, units=None, roster=None):
    dialog = LineChangeDialog(
        units
        if units is not None
        else {"home": [("Line 1", _LINE_1), ("PP 1", _PP_1)], "away": []},
        roster if roster is not None else _ROSTER,
    )
    qtbot.addWidget(dialog)
    return dialog


def _ok(qtbot, dialog):
    qtbot.mouseClick(dialog.ok_button, Qt.MouseButton.LeftButton)


def test_defaults_to_home_side_bringing_the_first_unit_on(qtbot):
    dialog = _make_dialog(qtbot)

    _ok(qtbot, dialog)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.team_side == "home"
    assert dialog.on_ice is True
    assert dialog.unit == _LINE_1
    assert dialog.jersey_numbers == []


def test_picking_another_unit_and_off(qtbot):
    dialog = _make_dialog(qtbot)
    dialog.unit_combo.setCurrentIndex(1)
    dialog.direction_combo.setCurrentIndex(dialog.direction_combo.findData(False))

    _ok(qtbot, dialog)

    assert dialog.unit == _PP_1
    assert dialog.on_ice is False


def test_jersey_field_is_only_enabled_for_the_ad_hoc_choice(qtbot):
    dialog = _make_dialog(qtbot)

    assert dialog.jersey_field.isEnabled() is False
    assert dialog.roster_list.isEnabled() is False
    dialog.unit_combo.setCurrentIndex(dialog.unit_combo.count() - 1)
    assert dialog.jersey_field.isEnabled() is True
    assert dialog.roster_list.isEnabled() is True


def test_a_side_with_no_units_falls_back_to_ad_hoc_only(qtbot):
    dialog = _make_dialog(qtbot)

    dialog.side_combo.setCurrentIndex(dialog.side_combo.findData("away"))

    assert dialog.unit_combo.count() == 1
    assert dialog.jersey_field.isEnabled() is True


def test_ad_hoc_roster_list_shows_the_chosen_sides_roster(qtbot):
    dialog = _make_dialog(qtbot)

    dialog.side_combo.setCurrentIndex(dialog.side_combo.findData("away"))

    labels = [
        dialog.roster_list.item(row).text() for row in range(dialog.roster_list.count())
    ]
    assert labels == ["#7", "#12 Diaz"]


def test_ad_hoc_multi_selects_from_the_roster(qtbot):
    dialog = _make_dialog(qtbot)
    dialog.side_combo.setCurrentIndex(dialog.side_combo.findData("away"))

    dialog.roster_list.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.roster_list.item(1).setCheckState(Qt.CheckState.Checked)
    _ok(qtbot, dialog)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.team_side == "away"
    assert dialog.unit is None
    assert dialog.jersey_numbers == [7, 12]


def test_ad_hoc_combines_roster_picks_with_typed_unrostered_jerseys(qtbot):
    dialog = _make_dialog(qtbot)
    dialog.side_combo.setCurrentIndex(dialog.side_combo.findData("away"))

    dialog.roster_list.item(1).setCheckState(Qt.CheckState.Checked)
    dialog.jersey_field.setText("30")
    _ok(qtbot, dialog)

    assert dialog.jersey_numbers == [12, 30]


def test_ad_hoc_parses_space_or_comma_separated_jerseys(qtbot):
    dialog = _make_dialog(qtbot)
    dialog.side_combo.setCurrentIndex(dialog.side_combo.findData("away"))
    dialog.jersey_field.setText("7, 12 30")

    _ok(qtbot, dialog)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.team_side == "away"
    assert dialog.unit is None
    assert dialog.jersey_numbers == [7, 12, 30]


def test_ad_hoc_rejects_an_empty_or_non_numeric_selection(qtbot):
    dialog = _make_dialog(qtbot, {"home": [], "away": []}, {"home": [], "away": []})

    for text in ("", "12 abc"):
        dialog.jersey_field.setText(text)
        _ok(qtbot, dialog)
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.error_label.text() != ""
