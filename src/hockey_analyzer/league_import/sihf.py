"""The SIHF league site (`sihf.ch`) as an import source: turning a game link
into a game ID, and scraping the two documents league import reads for it
(see ADR-0005) -- the server-generated game PDF export (metadata, score,
lineups) and the `game/{id}` HTML overview page (each team's `league_id`,
which the PDF doesn't carry as text).

Both are markup scrapes, not a versioned contract, so every parse failure
here raises a `LeagueSourceError` rather than returning partial data -- the
caller decides how far a failed source degrades the import.
"""

from __future__ import annotations

import io
import re
import urllib.request
from dataclasses import dataclass
from datetime import date as date_
from html.parser import HTMLParser

from pypdf import PdfReader

from hockey_analyzer.domain.enums import Position, Side


class LeagueSourceError(Exception):
    """A league-site document couldn't be fetched or didn't parse."""


class InvalidGameLinkError(ValueError):
    """The text given as a game link isn't a league game link (or ID)."""


# `https://www.sihf.ch/{lang}/game-center/game/[{tab}/]{game_id}` -- any
# language, any per-game tab (e.g. `aufstellung`), scheme optional.
_GAME_LINK = re.compile(
    r"^(?:https?://)?(?:www\.)?sihf\.ch/[a-z]{2}/game-center/game/"
    r"(?:[\w-]+/)?(\d+)/?(?:[?#].*)?$"
)
_BARE_GAME_ID = re.compile(r"^\d+$")


def parse_game_link(link: str) -> str:
    """The league game ID a pasted game link (or bare ID) points at."""
    link = link.strip()
    if _BARE_GAME_ID.match(link):
        return link
    match = _GAME_LINK.match(link)
    if match is None:
        raise InvalidGameLinkError(f"not a league game link: {link!r}")
    return match.group(1)


@dataclass(frozen=True)
class ScrapedPlayer:
    jersey_number: int
    # As the export prints it ("Last First"), markers like "(C)" removed.
    full_name: str
    # The position group the player lined up in for this game.
    position: Position


@dataclass(frozen=True)
class ScrapedTeam:
    name: str
    lineup: tuple[ScrapedPlayer, ...]


@dataclass(frozen=True)
class ScrapedGame:
    """What the PDF export yields for one game, before any matching."""

    league_id: str
    date: date_ | None
    venue: str | None
    home_score: int
    away_score: int
    # One {"home": int, "away": int} per period, like Game.period_scores.
    period_scores: list[dict[str, int]]
    home: ScrapedTeam
    away: ScrapedTeam

    def team(self, side: Side) -> ScrapedTeam:
        return self.home if side == Side.HOME else self.away


# -- PDF export ---------------------------------------------------------------


@dataclass(frozen=True)
class _Fragment:
    """One positioned run of text on a PDF page, in PDF user-space points
    (origin bottom-left)."""

    x: float
    y: float
    text: str


def parse_game_pdf(pdf: bytes) -> ScrapedGame:
    pages = _pdf_fragments(pdf)
    summary_page = _page_with(pages, "Spielort")
    home_score, away_score = _final_score(summary_page)
    game_id = _game_id(summary_page)
    home, away = _lineups(_page_with(pages, "Team-Lineups"))
    return ScrapedGame(
        league_id=game_id.text,
        date=_game_date(summary_page, game_id),
        venue=_below(summary_page, "Spielort"),
        home_score=home_score,
        away_score=away_score,
        period_scores=_period_scores(summary_page),
        home=home,
        away=away,
    )


_GAME_ID = re.compile(r"^\d{8,}$")
# "Samstag, 05.09.2026 15:30" -- the weekday is in the export's language.
_GAME_DATE = re.compile(r"^[^\W\d]+, (\d{2})\.(\d{2})\.(\d{4})\b")
_SCORE = re.compile(r"^\d{1,3}$")
_PERIOD_SCORES = re.compile(r"^\d+:\d+(\s*\|\s*\d+:\d+)+$")


def _game_id(summary_page: list[_Fragment]) -> _Fragment:
    for fragment in summary_page:
        if _GAME_ID.match(fragment.text):
            return fragment
    raise LeagueSourceError("no game ID on the summary page")


def _game_date(summary_page: list[_Fragment], game_id: _Fragment) -> date_ | None:
    """The game's own date sits in the game ID's column; the same format
    elsewhere on the page is the "last changed" timestamp."""
    for fragment in summary_page:
        match = _GAME_DATE.match(fragment.text)
        if match and abs(fragment.x - game_id.x) < 1:
            day, month, year = (int(part) for part in match.groups())
            return date_(year, month, day)
    return None


def _below(page: list[_Fragment], label: str) -> str | None:
    """The value printed directly under `label`, in the same column."""
    anchor = next((f for f in page if f.text == label), None)
    if anchor is None:
        return None
    under = [f for f in page if abs(f.x - anchor.x) < 1 and f.y < anchor.y - 1]
    return max(under, key=lambda f: f.y).text if under else None


def _final_score(summary_page: list[_Fragment]) -> tuple[int, int]:
    """The big "7 : 1" header prints each number as its own fragment, on
    the only row of the page holding exactly two bare numbers (the page
    number sits alone on its row). Blank in the pre-game template -- an
    unfinished game isn't importable (ticket 11)."""
    rows: dict[float, list[_Fragment]] = {}
    for fragment in summary_page:
        if _SCORE.match(fragment.text):
            rows.setdefault(round(fragment.y), []).append(fragment)
    pairs = [row for row in rows.values() if len(row) == 2]
    if len(pairs) != 1:
        raise LeagueSourceError("no final score (game not finished?)")
    home, away = sorted(pairs[0], key=lambda f: f.x)
    return int(home.text), int(away.text)


def _period_scores(summary_page: list[_Fragment]) -> list[dict[str, int]]:
    for fragment in summary_page:
        if _PERIOD_SCORES.match(fragment.text):
            return [
                {"home": int(home), "away": int(away)}
                for home, away in (
                    period.strip().split(":") for period in fragment.text.split("|")
                )
            ]
    raise LeagueSourceError("no period-by-period score")


def _lineups(lineup_page: list[_Fragment]) -> tuple[ScrapedTeam, ScrapedTeam]:
    """Both teams' names and lineups, home first."""
    title = _find(lineup_page, "Team-Lineups")
    # The row directly under the "Team-Lineups" title holds both team
    # names: home on the left half of the page, away on the right.
    below_title = [f for f in lineup_page if f.y < title.y - 1]
    if not below_title:
        raise LeagueSourceError("no team names under the lineup title")
    names_y = max(f.y for f in below_title)
    names = sorted(
        (f for f in below_title if abs(f.y - names_y) < 1), key=lambda f: f.x
    )
    if len(names) != 2:
        raise LeagueSourceError(f"expected two team names, found {len(names)}")
    home_name, away_name = names
    lineup_area = [f for f in below_title if f.y < names_y - 1]
    # Everything from the away team's name rightward is the away half.
    home_area = [f for f in lineup_area if f.x < away_name.x - 1]
    away_area = [f for f in lineup_area if f.x >= away_name.x - 1]
    return (
        ScrapedTeam(home_name.text, _parse_lineup(home_area)),
        ScrapedTeam(away_name.text, _parse_lineup(away_area)),
    )


# The six position-group headers every lineup uses (ticket 11), plus the
# sections after them whose rows aren't game participants. Compared
# casefolded: the PDF capitalizes "Stürmer Links", the HTML "links".
_LINEUP_HEADERS: dict[str, Position | None] = {
    "goalie": Position.GOALIE,
    "verteidiger links": Position.DEFENSE,
    "verteidiger rechts": Position.DEFENSE,
    "stürmer mitte": Position.CENTER,
    "stürmer links": Position.LEFT_WING,
    "stürmer rechts": Position.RIGHT_WING,
    "head coach": None,
    "absenzen": None,
}

_PLAYER_ROW = re.compile(r"^(\d{1,3})\s*-\s*(.+)$")
# Trailing markers after a name: captain "(C)", first goalie "(1st)", etc.
_NAME_MARKERS = re.compile(r"(\s*\([^()]*\))+$")


def _parse_lineup(team_area: list[_Fragment]) -> tuple[ScrapedPlayer, ...]:
    """One team's half of the lineup page. Each player row belongs to the
    nearest header row above it, and -- where a row pairs two headers
    (links/rechts) -- to the header horizontally closest to it, since each
    header is centered over its own column."""
    headers = [f for f in team_area if f.text.casefold() in _LINEUP_HEADERS]
    placed = []
    for fragment in team_area:
        row = _PLAYER_ROW.match(fragment.text)
        if not row:
            continue
        above = [h for h in headers if h.y > fragment.y + 1]
        if not above:
            continue
        nearest_y = min(h.y for h in above)
        header = min(
            (h for h in above if abs(h.y - nearest_y) < 1),
            key=lambda h: abs(h.x - fragment.x),
        )
        position = _LINEUP_HEADERS[header.text.casefold()]
        if position is None:
            continue
        name = _NAME_MARKERS.sub("", row.group(2)).strip()
        player = ScrapedPlayer(int(row.group(1)), name, position)
        # Listing order: section by section, left column before right,
        # then top to bottom -- the order the export reads in.
        placed.append(((-header.y, header.x, -fragment.y), player))
    if not placed:
        # Also the pre-game export's shape: a structurally empty template.
        raise LeagueSourceError("a team's lineup has no players")
    return tuple(player for _, player in sorted(placed, key=lambda p: p[0]))


def _pdf_fragments(pdf: bytes) -> list[list[_Fragment]]:
    try:
        reader = PdfReader(io.BytesIO(pdf))
        pages = []
        for page in reader.pages:
            fragments: list[_Fragment] = []

            def visit(text, cm, tm, _font_dict, _font_size, fragments=fragments):
                text = text.strip()
                if text:
                    x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                    y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                    fragments.append(_Fragment(x, y, text))

            page.extract_text(visitor_text=visit)
            pages.append(fragments)
    # pypdf raises a wide, undocumented range of exception types on
    # malformed input; any of them just means "not a readable export".
    except Exception as error:
        raise LeagueSourceError(f"unreadable PDF: {error}") from error
    return pages


def _page_with(pages: list[list[_Fragment]], text: str) -> list[_Fragment]:
    for fragments in pages:
        if any(f.text == text for f in fragments):
            return fragments
    raise LeagueSourceError(f"no page containing {text!r}")


def _find(fragments: list[_Fragment], text: str) -> _Fragment:
    for fragment in fragments:
        if fragment.text == text:
            return fragment
    raise LeagueSourceError(f"no {text!r} on the page")


# -- fetching -------------------------------------------------------------------


_SITE = "https://www.sihf.ch"


def _game_page_url(game_id: str) -> str:
    return f"{_SITE}/de/game-center/game/{game_id}"


class SihfHttpSource:
    """The live `LeagueSource`: plain unauthenticated GETs (ticket 02). The
    PDF endpoint answers 400 without a `Referer` naming the game's page --
    hotlink protection, not auth (ADR-0005). The German page is requested
    because the parsers match the export's German labels."""

    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self._timeout = timeout_seconds

    def fetch_game_pdf(self, game_id: str) -> bytes:
        return self._get(
            f"{_SITE}/umbraco/GameCenter/GameDetail/ExportGameTimeline"
            f"?gameId={game_id}",
            referer=_game_page_url(game_id),
        )

    def fetch_game_page(self, game_id: str) -> str:
        return self._get(_game_page_url(game_id)).decode("utf-8", errors="replace")

    def _get(self, url: str, *, referer: str | None = None) -> bytes:
        headers = {"User-Agent": "hockey-analyzer"}
        if referer is not None:
            headers["Referer"] = referer
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=self._timeout
            ) as response:
                return response.read()
        # URLError covers HTTPError (non-2xx) and connection failures;
        # OSError covers timeouts and resets mid-read.
        except OSError as error:
            raise LeagueSourceError(f"couldn't fetch {url}: {error}") from error


# -- HTML overview page -------------------------------------------------------


_TEAM_PATH = re.compile(r"/game-center/team/([^/?#\s]+)")


class _HeaderTeamLinks(HTMLParser):
    """Collects the header's `c-game-detail-header__team--home/--away`
    anchors' `team/{league_id}` paths."""

    def __init__(self) -> None:
        super().__init__()
        self.league_ids: dict[Side, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        match = _TEAM_PATH.search(attributes.get("href") or "")
        for side in Side:
            if f"c-game-detail-header__team--{side.value}" in classes and match:
                self.league_ids.setdefault(side, match.group(1))


def parse_team_league_ids(html: str) -> dict[Side, str]:
    """Each side's team `league_id`, from the game page's header links --
    the opaque `team/{league}-{div}-{teamNum}` path segment (never parsed
    further, per ticket 02)."""
    parser = _HeaderTeamLinks()
    parser.feed(html)
    if set(parser.league_ids) != set(Side):
        raise LeagueSourceError("game page header has no home/away team links")
    return parser.league_ids
