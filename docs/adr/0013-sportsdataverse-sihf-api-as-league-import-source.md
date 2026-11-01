---
status: accepted
---

# League import reads the SIHF statistics API through sportsdataverse, not the PDF export

League game-link import now reads one game from the SIHF statistics API (`data.sihf.ch`, the `gameoverview` body) through the `sportsdataverse.hockey.sihf` module, replacing both the PDF-export scrape and the HTML-header scrape from ADR-0005. One structured response holds everything the two scrapes reconstructed between them: metadata, score, lineups and each team's identifier. It also adds things neither had: a stable per-player licence number (ADR-0014), lineups grouped by line slot, and the official play-by-play. Both scrapers and `pypdf` are deleted. A failed fetch falls back to manual entry, as before; there is no second source to fall back to.

We take `sportsdataverse` as a full runtime dependency even though it brings a large scientific stack (pandas, polars, pyarrow, scipy, scikit-learn, xgboost) into an app ADR-0012 describes as light. The project's author also wrote and maintains the upstream SIHF module, so fixes to SIHF parsing (the wrong-game check, drifting error shapes) live in one place instead of being copied here, and install size isn't a concern for how this app ships. It is pinned and stays behind the `LeagueSource` seam, so a thin stdlib client over the same JSON endpoint could replace it if that ever changes.

Considered: an optional extra (`hockey-analyzer[sihf]`), rejected because import is a core flow, not an add-on; and a thin client of our own, rejected because it would duplicate code the author already maintains upstream.
