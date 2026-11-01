---
status: accepted
---

# A Player's SIHF licence number is their league identity, across games and installs

ADR-0004 and the **Player** glossary entry rested on ticket 02's finding that the league exposes no stable per-player identifier. The SIHF statistics API (ADR-0013) does: every player carries their federation licence number. `Player` now gets an optional `league_id` holding it. Import matches a lineup player on it automatically and falls back to the manual `full_name` match only for players without one. A name match confirmed during import backfills the licence number onto the existing Player, so the manual step happens at most once per player. Report bundles carry the licence number when the player has one, so a recipient's install matches them automatically. Name and jersey number remain the fallback for players created by hand.

This supersedes ADR-0004. Its rejected alternative (minting our own cross-install ID) stays rejected: the identifier now comes from the league, not from us.
