"""Fixture builders for the SIHF league-site sources `LeagueImportService`
reads (see ADR-0005): the server-generated game PDF export and the
`game/{id}` HTML overview page.

`sihf_game_pdf` writes a real (minimal) PDF whose text fragments sit at the
same coordinates the live export puts them -- teams split left/right,
position-group headers centered over their columns -- so the parser is
exercised against the export's actual layout, with fictional names. No
network access is involved anywhere here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# (jersey number, name as the export prints it -- "Last First", optionally
# followed by markers like "(C)" or "(1st)")
LineupPlayer = tuple[int, str]


@dataclass
class Lineup:
    goalies: list[LineupPlayer] = field(default_factory=list)
    defense_left: list[LineupPlayer] = field(default_factory=list)
    defense_right: list[LineupPlayer] = field(default_factory=list)
    centers: list[LineupPlayer] = field(default_factory=list)
    left_wings: list[LineupPlayer] = field(default_factory=list)
    right_wings: list[LineupPlayer] = field(default_factory=list)
    head_coach: str = "Coach Person"
    absences: list[LineupPlayer] = field(default_factory=list)


GAME_ID = "20270009263101"
GAME_LINK = f"https://www.sihf.ch/de/game-center/game/{GAME_ID}"


def sample_home_lineup() -> Lineup:
    return Lineup(
        goalies=[(39, "Keller Jonas (1st)"), (41, "Meyer Luca")],
        defense_left=[(71, "Brunner Noah"), (25, "Weber Carl-Louis")],
        defense_right=[(22, "Frei Jean-Michel (C)"), (13, "Huber Livio Nicola")],
        centers=[(69, "Mémeteau Matthias"), (28, "Graf Marcel")],
        left_wings=[(6, "Métraux Dorian"), (55, "König Tim")],
        right_wings=[(11, "Würth Ezequiel"), (23, "Baumann Mathis")],
    )


def sample_away_lineup() -> Lineup:
    return Lineup(
        goalies=[(30, "Schmid Björn (1st)")],
        defense_left=[(5, "Roth Arnaud")],
        defense_right=[(26, "Eriksson Nilsson Karl Ludvig (E)")],
        centers=[(21, "Galley Noham")],
        left_wings=[(15, "Brogli Gaël")],
        right_wings=[(18, "Putallaz Romain")],
    )


def sihf_game_pdf(
    *,
    game_id: str = GAME_ID,
    home_name: str = "HC Château-d'Oex",
    away_name: str = "HC Monthey",
    home_score: int | None = 7,
    away_score: int | None = 1,
    period_scores: str = "4:1 | 3:0 | 0:0",
    game_date: str = "Samstag, 05.09.2026 15:30",
    venue: str = "Centre Sportif de la Patinoire",
    home_lineup: Lineup | None = None,
    away_lineup: Lineup | None = None,
) -> bytes:
    """The two-page export: page 1 carries metadata/score (plus the goal
    timeline, which import ignores), page 2 both teams' lineups. A `None`
    score reproduces the pre-game template's blank score."""
    home_lineup = sample_home_lineup() if home_lineup is None else home_lineup
    away_lineup = sample_away_lineup() if away_lineup is None else away_lineup

    summary = [
        (517.6, 809.0, "24.09.2026 15:43"),
        (26.6, 668.0, home_name),
        (485.6, 668.0, away_name),
        (26.6, 643.5, "2. Liga"),
        (26.6, 633.8, "Exhibition Games"),
        (26.6, 624.0, game_date),
        (26.6, 614.3, game_id),
        (182.3, 643.5, "Spielort"),
        (182.3, 633.8, venue),
        (182.3, 624.0, "Letzte Änderung"),
        (182.3, 614.3, "Samstag, 05.09.2026 17:53"),
        (317.0, 643.5, "Schiedsrichter"),
        (317.0, 633.8, "Ref Eree"),
        (26.6, 564.3, "Spielverlauf"),
        (188.2, 538.4, home_name),
        (316.9, 538.4, away_name),
        (277.5, 514.3, "1. Drittel"),
        (284.5, 491.3, "01:26"),
        (344.4, 496.1, "EQ / 0:1 - Putallaz Romain (1)"),
        (21.1, 18.7, "Swiss Ice Hockey Federation, "),
        (559.8, 18.7, "1"),
    ]
    if home_score is not None and away_score is not None:
        summary += [
            (252.5, 765.9, str(home_score)),
            (291.6, 773.1, ":"),
            (316.9, 765.9, str(away_score)),
            (248.1, 730.1, period_scores),
        ]

    lineups = [
        (21.1, 792.9, "Team-Lineups"),
        (447.3, 794.5, "Captain  "),
        (487.7, 794.5, "Topscorer  1. Torhüter"),
        (21.1, 779.3, home_name),
        (302.2, 779.3, away_name),
        *_lineup_fragments(home_lineup, x_offset=0.0),
        *_lineup_fragments(away_lineup, x_offset=281.1),
        (21.1, 18.7, "Swiss Ice Hockey Federation, "),
        (559.8, 18.7, "2"),
    ]
    return pdf_document([summary, lineups])


_ROW = 13.95


def _lineup_fragments(lineup: Lineup, *, x_offset: float) -> list:
    """Mirrors the export's lineup layout for one team: single centered
    columns for goalies/centers, paired left/right columns for defense and
    wings, then head coach and absences."""
    fragments = []
    y = 765.4

    def centered(header, players, header_x, player_x):
        nonlocal y
        fragments.append((header_x + x_offset, y, header))
        for index, (number, name) in enumerate(players, start=1):
            fragments.append(
                (player_x + x_offset, y - index * _ROW, f"{number} - {name}")
            )
        y -= (len(players) + 1) * _ROW + 14

    def paired(left_header, left, right_header, right):
        nonlocal y
        fragments.append((35.1 + x_offset, y, left_header))
        fragments.append((167.2 + x_offset, y, right_header))
        for column_x, players in ((21.1, left), (155.8, right)):
            for index, (number, name) in enumerate(players, start=1):
                fragments.append(
                    (column_x + x_offset, y - index * _ROW, f"{number} - {name}")
                )
        y -= (max(len(left), len(right)) + 1) * _ROW + 14

    centered("Goalie", lineup.goalies, 139.7, 115.9)
    paired(
        "Verteidiger links",
        lineup.defense_left,
        "Verteidiger rechts",
        lineup.defense_right,
    )
    centered("Stürmer Mitte", lineup.centers, 126.9, 110.2)
    paired("Stürmer Links", lineup.left_wings, "Stürmer Rechts", lineup.right_wings)
    # The coach's name carries no jersey number, unlike a player row.
    fragments.append((130.8 + x_offset, y, "Head Coach"))
    fragments.append((129.1 + x_offset, y - _ROW, lineup.head_coach))
    y -= 2 * _ROW
    centered("Absenzen", lineup.absences, 134.4, 110.2)
    return fragments


def pdf_document(pages: list[list[tuple[float, float, str]]]) -> bytes:
    """A minimal valid PDF: one Helvetica (WinAnsi) font, one content stream
    per page placing each fragment with an absolute `Td`."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # patched below once the page tree number is known
    pages_obj = add(b"")
    font = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    page_numbers = []
    for fragments in pages:
        content = b"".join(
            b"BT /F1 8 Tf %.1f %.1f Td (%s) Tj ET\n" % (x, y, _pdf_string(text))
            for x, y, text in fragments
        )
        stream = add(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)
        )
        page_numbers.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_obj, font, stream)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
    kids = b" ".join(b"%d 0 R" % number for number in page_numbers)
    objects[pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        kids,
        len(page_numbers),
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        xref_at,
    )
    return bytes(out)


def _pdf_string(text: str) -> bytes:
    raw = text.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def sihf_game_page(
    *,
    home_team_league_id: str = "10-4-103010",
    away_team_league_id: str = "10-4-104254",
    home_name: str = "HC Château-d'Oex",
    away_name: str = "HC Monthey",
) -> str:
    """The parts of the `game/{id}` overview page import reads: the header's
    home/away team links, whose `team/{id}` path is each team's league_id."""
    base = "https://www.sihf.ch/de/game-center/team"
    return f"""<!DOCTYPE html>
<html><body>
<div class="c-game-detail-header is-finished">
  <a href="{base}/{home_team_league_id}"
     class="c-game-detail-header__team c-game-detail-header__team--home">
    <span class="c-game-detail-header__team-name">{home_name}</span>
  </a>
  <div class="c-game-detail-header__score">7:1</div>
  <a href="{base}/{away_team_league_id}"
     class="c-game-detail-header__team c-game-detail-header__team--away">
    <span class="c-game-detail-header__team-name">{away_name}</span>
  </a>
</div>
<a class="c-gc-table__team c-gc-table__team-link" href="{base}/10-4-105750">x</a>
</body></html>"""


class FakeLeagueSource:
    """Stands in for the HTTP adapter: returns canned content, or raises the
    given exception, per source -- so each source can fail independently."""

    def __init__(self, *, pdf=None, page=None) -> None:
        self._pdf = sihf_game_pdf() if pdf is None else pdf
        self._page = sihf_game_page() if page is None else page
        self.requested_game_ids: list[str] = []

    def fetch_game_pdf(self, game_id: str) -> bytes:
        self.requested_game_ids.append(game_id)
        if isinstance(self._pdf, Exception):
            raise self._pdf
        return self._pdf

    def fetch_game_page(self, game_id: str) -> str:
        self.requested_game_ids.append(game_id)
        if isinstance(self._page, Exception):
            raise self._page
        return self._page
