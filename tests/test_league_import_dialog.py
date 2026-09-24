from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog
from sihf_fixtures import GAME_LINK, FakeLeagueSource
from sqlalchemy import func, select

from hockey_analyzer.domain.enums import RinkType, Side
from hockey_analyzer.domain.models import Game, GameRosterEntry, Player, Team
from hockey_analyzer.league_import import LeagueImportService, LeagueSourceError
from hockey_analyzer.ui.league_import_dialog import LeagueImportDialog


def _make_dialog(qtbot, session, source=None):
    service = LeagueImportService(session, source or FakeLeagueSource())
    dialog = LeagueImportDialog(service)
    qtbot.addWidget(dialog)
    return dialog


def _fetch(dialog, link=GAME_LINK):
    dialog.link_field.setText(link)
    dialog.fetch_button.click()


def _row_counts(session):
    return {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (Game, Team, Player, GameRosterEntry)
    }


def _cell_texts(table, column):
    return [table.item(row, column).text() for row in range(table.rowCount())]


# -- the review screen ---------------------------------------------------------


def test_review_is_hidden_until_a_link_is_fetched(qtbot, session):
    dialog = _make_dialog(qtbot, session)

    assert dialog.review_panel.isHidden()
    assert not dialog.confirm_button.isEnabled()


def test_fetching_shows_both_teams_and_every_roster_row_without_saving(qtbot, session):
    dialog = _make_dialog(qtbot, session)
    counts_before = _row_counts(session)

    _fetch(dialog)

    assert not dialog.review_panel.isHidden()
    assert "HC Château-d'Oex" in dialog.team_label(Side.HOME).text()
    assert "HC Monthey" in dialog.team_label(Side.AWAY).text()
    home = dialog.roster_table(Side.HOME)
    assert _cell_texts(home, 0)[:3] == ["39", "41", "71"]
    assert _cell_texts(home, 1)[:3] == ["Keller Jonas", "Meyer Luca", "Brunner Noah"]
    assert dialog.roster_table(Side.AWAY).rowCount() == 6
    assert _row_counts(session) == counts_before


def test_game_summary_shows_date_venue_and_score(qtbot, session):
    dialog = _make_dialog(qtbot, session)

    _fetch(dialog)

    summary = dialog.game_summary_label.text()
    assert "2026-09-05" in summary
    assert "Centre Sportif de la Patinoire" in summary
    assert "7 : 1" in summary


def test_each_row_shows_whether_it_links_or_creates_a_player(qtbot, session):
    session.add_all([Player(full_name="Galley Noham"), Player(full_name="Rot Arnaud")])
    session.commit()
    dialog = _make_dialog(qtbot, session)

    _fetch(dialog)

    away_status = _cell_texts(dialog.roster_table(Side.AWAY), 3)
    names = _cell_texts(dialog.roster_table(Side.AWAY), 1)
    assert away_status[names.index("Galley Noham")] == "Linked"
    # A near miss is surfaced, not silently merged.
    assert away_status[names.index("Roth Arnaud")] == "New — possible match"
    assert away_status[names.index("Brogli Gaël")] == "New"


def test_each_team_shows_whether_it_links_or_creates_a_team(qtbot, session):
    session.add(Team(name="Château", league_id="10-4-103010"))
    session.commit()
    dialog = _make_dialog(qtbot, session)

    _fetch(dialog)

    assert "linked" in dialog.team_status_label(Side.HOME).text().lower()
    assert "new" in dialog.team_status_label(Side.AWAY).text().lower()


# -- which team is the user's ---------------------------------------------------


def test_confirm_waits_for_an_explicit_user_team_answer(qtbot, session):
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)

    # Nothing to pre-fill from: two new teams.
    assert dialog.checked_user_team() is dialog.UNANSWERED
    assert not dialog.confirm_button.isEnabled()

    dialog.user_team_button(Side.AWAY).click()

    assert dialog.confirm_button.isEnabled()


def test_user_team_is_prefilled_from_a_linked_team_already_flagged(qtbot, session):
    session.add(Team(name="Mine", league_id="10-4-104254", is_user_team=True))
    session.commit()
    dialog = _make_dialog(qtbot, session)

    _fetch(dialog)

    assert dialog.checked_user_team() == Side.AWAY
    assert dialog.confirm_button.isEnabled()


def test_neither_is_an_explicit_answer(qtbot, session):
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)

    dialog.user_team_button(None).click()
    dialog.confirm_button.click()

    game = session.get(Game, dialog.game_id)
    assert not game.home_team.is_user_team
    assert not game.away_team.is_user_team


# -- confirming -----------------------------------------------------------------


def test_confirming_imports_the_game_as_reviewed_and_closes(qtbot, session):
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    dialog.user_team_button(Side.HOME).click()
    dialog.rink_type_combo.setCurrentIndex(
        dialog.rink_type_combo.findData(RinkType.NHL.value)
    )

    dialog.confirm_button.click()

    assert dialog.result() == QDialog.DialogCode.Accepted
    game = session.get(Game, dialog.game_id)
    assert game.league_id == "20270009263101"
    assert game.rink_type == RinkType.NHL
    assert game.home_team.is_user_team
    assert len(game.roster_entries) == 12 + 6


def test_uncertain_team_match_must_be_confirmed_or_corrected(qtbot, session):
    existing = Team(name="HC Monthey")
    session.add(existing)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    dialog.user_team_button(None).click()

    assert not dialog.confirm_button.isEnabled()
    combo = dialog.team_combo(Side.AWAY)
    combo.setCurrentIndex(combo.findData(existing.id))
    assert dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()

    assert session.get(Game, dialog.game_id).away_team_id == existing.id


def test_uncertain_team_match_can_be_corrected_to_a_new_team(qtbot, session):
    existing = Team(name="HC Monthey")
    session.add(existing)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    dialog.user_team_button(None).click()

    combo = dialog.team_combo(Side.AWAY)
    combo.setCurrentIndex(combo.findData(dialog.NEW))
    dialog.confirm_button.click()

    away = session.get(Game, dialog.game_id).away_team
    assert away.id != existing.id
    assert away.league_id == "10-4-104254"


def test_confirming_a_flagged_candidate_prefills_the_user_team(qtbot, session):
    existing = Team(name="HC Monthey", is_user_team=True)
    session.add(existing)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    assert dialog.checked_user_team() is dialog.UNANSWERED

    combo = dialog.team_combo(Side.AWAY)
    combo.setCurrentIndex(combo.findData(existing.id))

    assert dialog.checked_user_team() == Side.AWAY


def test_a_prefill_is_withdrawn_when_its_team_is_swapped_for_a_new_one(qtbot, session):
    existing = Team(name="HC Monthey", is_user_team=True)
    session.add(existing)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    combo = dialog.team_combo(Side.AWAY)
    combo.setCurrentIndex(combo.findData(existing.id))

    combo.setCurrentIndex(combo.findData(dialog.NEW))

    # A new team is never made the user's without them saying so.
    assert dialog.checked_user_team() is dialog.UNANSWERED


def test_the_users_own_answer_survives_later_team_changes(qtbot, session):
    existing = Team(name="HC Monthey", is_user_team=True)
    session.add(existing)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    dialog.user_team_button(Side.HOME).click()

    combo = dialog.team_combo(Side.AWAY)
    combo.setCurrentIndex(combo.findData(existing.id))

    assert dialog.checked_user_team() == Side.HOME


def test_a_new_player_row_can_be_linked_to_a_possible_match(qtbot, session):
    near_miss = Player(full_name="Rot Arnaud")
    session.add(near_miss)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    dialog.user_team_button(None).click()
    row = dialog.roster_index("Roth Arnaud")

    combo = dialog.player_combo(row)
    assert combo.currentData() == dialog.NEW
    combo.setCurrentIndex(combo.findData(near_miss.id))
    dialog.confirm_button.click()

    game = session.get(Game, dialog.game_id)
    entry = next(e for e in game.roster_entries if e.jersey_number == 5)
    assert entry.player_id == near_miss.id


def test_any_existing_player_can_be_searched_for_and_linked(qtbot, session):
    unrelated = Player(full_name="Totally Different")
    session.add(unrelated)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)

    combo = dialog.player_combo(dialog.roster_index("Brogli Gaël"))

    assert combo.isEditable()
    assert combo.findData(unrelated.id) >= 0


def test_a_silently_linked_player_can_be_corrected_to_new(qtbot, session):
    namesake = Player(full_name="Galley Noham")
    session.add(namesake)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    dialog.user_team_button(None).click()

    combo = dialog.player_combo(dialog.roster_index("Galley Noham"))
    assert combo.currentData() == namesake.id
    combo.setCurrentIndex(combo.findData(dialog.NEW))
    dialog.confirm_button.click()

    game = session.get(Game, dialog.game_id)
    entry = next(e for e in game.roster_entries if e.jersey_number == 21)
    assert entry.player_id != namesake.id


def test_a_refused_confirmation_shows_why_and_stays_open(qtbot, session):
    player = Player(full_name="Someone")
    session.add(player)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    dialog.user_team_button(None).click()
    for row in (0, 1):
        combo = dialog.player_combo(row)
        combo.setCurrentIndex(combo.findData(player.id))
    counts_before = _row_counts(session)

    dialog.confirm_button.click()

    assert dialog.error_label.text()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.game_id is None
    assert _row_counts(session) == counts_before


# -- links that can't be imported ------------------------------------------------


def test_a_link_that_isnt_a_league_game_shows_an_error(qtbot, session):
    dialog = _make_dialog(qtbot, session)

    _fetch(dialog, "https://example.com/not-a-game")

    assert dialog.error_label.text()
    assert dialog.review_panel.isHidden()


@pytest.mark.parametrize(
    "pdf", [LeagueSourceError("HTTP 400"), b"not a pdf"], ids=["fetch", "parse"]
)
def test_an_unreadable_game_offers_manual_entry_instead(qtbot, session, pdf):
    dialog = _make_dialog(qtbot, session, FakeLeagueSource(pdf=pdf))

    _fetch(dialog)

    assert dialog.review_panel.isHidden()
    assert not dialog.manual_entry_button.isHidden()
    dialog.manual_entry_button.click()
    assert dialog.fall_back_to_manual
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.game_id is None


def test_an_already_imported_game_points_at_the_existing_one(qtbot, session):
    existing = Game(league_id="20270009263101")
    session.add(existing)
    session.commit()
    dialog = _make_dialog(qtbot, session)
    counts_before = _row_counts(session)

    _fetch(dialog)

    assert dialog.review_panel.isHidden()
    assert f"#{existing.id}" in dialog.error_label.text()
    dialog.open_existing_button.click()
    assert dialog.game_id == existing.id
    assert not dialog.fall_back_to_manual
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert _row_counts(session) == counts_before


def test_fetching_again_replaces_the_previous_review(qtbot, session):
    dialog = _make_dialog(qtbot, session)
    _fetch(dialog)
    dialog.user_team_button(Side.HOME).click()

    _fetch(dialog)

    assert dialog.roster_table(Side.AWAY).rowCount() == 6
    assert dialog.checked_user_team() is dialog.UNANSWERED
