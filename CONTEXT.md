# Hockey Analyzer — Context

## Glossary

### Event

The atomic fact recorded from a game: something observed at a specific moment in the footage. Every event carries a **video timestamp** (required, always), a `source` (`manual` or `vision`), and a `confirmed` flag, so vision-model output can be merged with manually-tagged events without a schema change. Event subtypes: `faceoff`, `shot_attempt`, `penalty`, `shift_change`, `stoppage`, `period_start`, `period_end`.

### Video timestamp

An offset into a specific game's footage file. The one time reference every event always carries. Distinct from the **game clock**, which is derived, not stored.

### Game clock

The period number and time-remaining-in-period a hockey viewer would recognize from a scoreboard. Not stored directly on events — derived from `period_start`/`period_end`, `stoppage`, and the events that imply a stoppage (`faceoff` marks resumption of play; `shot_attempt` with outcome `goal` and `penalty` each imply a stoppage occurred, without requiring a separate logged `stoppage` event for those cases). Elapsed live time within a period = footage time within the period minus the sum of `[stoppage → next resumption]` gaps. This derivation only holds because the footage is continuous, non-stop recording from period start to period end — a game recorded with cuts (e.g. intermissions edited out inconsistently) would break it.

### Stoppage

An event marking a whistle that halts play, logged **only** when the cause isn't already implied by another logged event (icing, offside, puck out of play, etc.). A goal or penalty is itself a stoppage-implying event and does not get an additional standalone `stoppage` event for the same whistle.

### Shift / shift_change

A **shift** (a continuous stretch a player spends on the ice) is never stored directly — it's derived from consecutive `shift_change` events for that player. A `shift_change` event is the primitive: one player, one team, one video timestamp, and whether they came on or off. This was chosen over storing shift intervals directly because it matches how shifts are actually observed while tagging (you see a player step off, not a pre-known interval) and composes better with partial/incomplete tagging.

### Strength state

The number of skaters each team has on ice (5v5, power play, 4v4, empty net, etc.), stored **explicitly on every event** at tagging time rather than derived from the penalty timeline. Chosen because manual tagging won't always have complete, gap-free penalty data, and deriving strength state would leave it wrong or unknown for events tagged before their governing penalty is entered.

### Shot attempt

The unified event type for anything Corsi/Fenwick/PDO count: a single `shot_attempt` event with an `outcome` field (`goal` / `saved` / `missed` / `blocked`). A **goal** is not a separate event type — it's a `shot_attempt` with `outcome: goal`, additionally carrying `assist1`/`assist2` (optional) player references. Chosen because Corsi, Fenwick, and PDO are all defined as filters over the same underlying population of shot attempts, and a unified event avoids keeping a shot and its resulting goal in sync as two linked records.

### Rink coordinates

Every event with a physical location on the ice (currently: `faceoff`, `shot_attempt`) stores an (x, y) coordinate on a standardized rink, rather than a coarse zone enum. `zone` (defensive/neutral/offensive, relative to a given team) is always derivable from coordinates via fixed rink geometry — it is never stored independently. Chosen for tagging consistency (the tagger always clicks a point) and so a future event type can become coordinate-aware without a modeling change. "Standardized" means standardized *per the game's* **Rink type** — there is no single implicit rink shape; `zone`/`high_danger` resolve their geometry against whichever standard that game was tagged under.

### Attacking direction

Which end of the rink (toward +x or −x in **Rink coordinates**) a team is shooting at during a given period — what turns a raw faceoff location into "offensive" or "defensive" *for that team* (e.g. for **Zone start (%)**). Never stored and never asked of the tagger: teams switch ends every period, and the tagger just clicks raw coordinates. Derived per team, per period (periods delimited by `period_start` events), from where that team's own `shot_attempt`s land that period, by majority — nearly every attempt is taken in the offensive half, so a stray dump-in doesn't flip it. A team with no majority in a period (no located attempts, or a tie) attacks the end opposite the other team's; with no majority from either team, the direction is unknown and any zone that depends on it is reported as undetermined, never guessed.

### Rink type

A closed choice (`IIHF` / `NHL`) on `Game`, fixing which physical rink-dimension standard **Rink coordinates** derives every one of that game's location-bearing events against. Set once at game creation and immutable afterward — unlike every other manually-tagged field in this app, which is correctable at any time, changing it later would silently reinterpret every already-clicked (x, y) coordinate against a different-shaped rink, with nothing re-clicked and no warning; the correct fix for a wrong choice is deleting the game and starting over. Distinct from `Team.league_id` (an external league-API identifier used to prefill data on import): a team's league doesn't determine which physical rink a specific game was played on.

### Shot type

A required, closed enum field on `shot_attempt`: `wrist` / `slap` / `snap` / `backhand` / `tip` / `wrap-around` / `unknown`. `tip` covers deflections too — distinguishing a tip-in from a full deflection off footage is a judgment call not worth forcing on a tagger. `unknown` is an explicit value (not nullability), so the field never blocks a tagger who genuinely can't tell.

### Shot context

Four independent boolean fields on `shot_attempt` — `rush`, `rebound`, `screened`, `one_timer` — not mutually exclusive (a shot can be several at once: a screened one-timer off a rebound). Each defaults `false`, and `false` is a real "no," not a missing-data marker (the event's existing `confirmed` flag already covers "not yet reviewed"). Stored explicitly at tagging time rather than derived from event sequence — e.g. no time-window heuristic inferring "rebound" from a prior `shot_attempt` — matching the precedent **Strength state** already sets: manual tagging can't reliably supply the inputs a derivation would need. Treated as manual-tagging-primary; whether a future vision pipeline can also populate them is that pipeline's problem to solve, not this schema's.

### Danger zone

`high_danger`, a boolean on `shot_attempt`, derived (never stored) from the event's (x, y) coordinates via fixed rink geometry — geometry only, not factoring in shot type or **Shot context**. Binary only, no low/medium tiers — nothing currently scoped needs finer granularity than what **Goalie stats**' HD SV% requires. Same "derived from coordinates" treatment as **Rink coordinates**' `zone`.

### xG (expected goals)

`shot_attempt.xg`, a nullable float in `[0, 1]`. Populated only for shots-on-goal outcomes (`goal` / `saved`) — `missed`/`blocked` shots leave it `null`, matching **PDO**'s shots-on-goal-only population (a blocked shot never reached the goalie, so it was never really "faced"). The field exists in the schema now and stays `null` until the xG model is calibrated (deferred past this spec, same treatment RAPM's regression gets).

### Team

A tracked hockey team, identified by an internal ID — never by `name` alone, since names can be renamed or entered inconsistently across seasons. Carries a mutable `name` label, an optional `league_id` (the identifier the league app/API uses for this team, populated when a game is created from a league game link — see [League API integration contract](.scratch/hockey-analyzer-spec/issues/11-league-api-integration-contract.md)), and an `is_user_team` flag. `is_user_team` marks a team as the user's own for stats purposes (full individual advanced stats: RAPM, +/-) rather than the lighter team-level reports other tracked teams get; it's a boolean per Team rather than a single app-wide pointer, so nothing rules out more than one team being flagged.

### Player

An individual tracked across games (and, later, seasons), identified by an internal ID — never by jersey number, which is reused across teams and over time on the same team. Carries a `full_name` (the attribute a tagger searches against when deciding whether a player already has a record, or whether to create a new one — this matching is a manual judgment call at data-entry time, not automated identity resolution) and `handedness` (which side the player shoots from). Unlike **Team**, Player carries **no** `league_id`: the [league API investigation](.scratch/hockey-analyzer-spec/issues/02-league-api-investigation.md) found the league site exposes no stable per-player identifier anywhere (game page, lineup, player-stats tabs, team roster) — only jersey number and name text. A league-app roster pull can prefill `full_name` and jersey number, but recognizing a returning player is always the manual `full_name` matching described above; there is no automated identity-resolution path for players, now or by design.

### Game roster entry

The single record of a **Player**'s participation in one game: `(game_id, player_id, team_id, jersey_number)`. There is no separate, longer-lived "team membership" concept — a player's team is only ever known in the context of a specific game, recorded once per game rather than as a date-bounded stint, since that's the granularity the data actually arrives at (from tagging or from a league-API roster pull) and a stint would just duplicate what every `GameRosterEntry` already states. A player's team "as of" any given game is read directly off that game's entry, not inferred from a most-recent-stint lookup. `jersey_number` is unique within `(game, team)` — two teammates sharing a number in the same game is a tagging error, not a legitimate case, and is rejected rather than tolerated. A `GameRosterEntry` (and the `Player` it references) can be created on the fly, mid-tagging, the first time that player appears in an event — tagging never requires a complete roster to exist upfront. Applies uniformly to the home team and all other tracked teams; there is no player-less attribution path for opponents (see **Team**) — the "lighter reports" for non-home teams is a difference in which stats get computed, not in what gets recorded.

Settled while designing [League API integration contract](.scratch/hockey-analyzer-spec/issues/11-league-api-integration-contract.md): a league-site roster import also exposes the position the player lined up at *for that specific game* (the site's lineup page groups by side — center/left/right forward, left/right defense — which can differ game-to-game from a player's nominal **Position**). This is stored as an optional, informational `GameRosterEntry` field, populated only by league-API import (never by manual tagging). It never writes to `Player.position` — that field stays a separately, manually-maintained fixed attribute per **Position**, untouched by import, even at first player creation.

### Unknown player reference

Any player-reference field (`shift_change`'s player, `shot_attempt`'s shooter/`assist1`/`assist2`, `faceoff`'s participants, `penalty`'s player) can be set to an explicit `unknown` value instead of a `Player` ID — the same treatment **Shot type**'s `unknown` enum value gets: an explicit answer, not a missing-data marker, so a tagger who can't read an illegible or obstructed jersey number isn't blocked from logging the event. Permanently acceptable for opposing-team players, matching the lighter completeness bar the map already sets for non-home teams (see **Opponent shifts complete**). For home-team players it's expected to be resolved eventually (typically in a later tagging pass) but is never a hard block: an unresolved home-team `unknown` degrades only the individual stat that needed that exact field — reported as incomplete, never silently dropped — the same "narrows, never silently" precedent **Opponent shifts complete** already sets (see [ADR-0002](docs/adr/0002-opponent-shift-completeness-flag.md)).

### Corsi / Fenwick

Possession stats built over the `shot_attempt` population: **Corsi** counts every attempt (`goal`/`saved`/`missed`/`blocked`); **Fenwick** is the same but excludes `blocked` attempts. Both are reported as four derived quantities — For, Against, Differential (For−Against), Percentage (For/(For+Against)) — at a caller-chosen `strength_state` filter (default 5v5). Computed team-wide for every tracked team, and additionally per-player (while that player is on ice, derived from their own team's `shift_change` events) for the home team by default, or for an opposing team once that game's `opponent_shifts_complete` flag is set — see below.

### PDO

`(shooting% + save%) × 1000`, a team-only stat (no individual/on-ice variant), computed over shots-on-goal only (`shot_attempt` events with outcome `goal` or `saved` — `missed`/`blocked` excluded), at a caller-chosen `strength_state` filter (default 5v5). Deliberately narrower than Corsi/Fenwick's population, matching the stat's standard, universally-recognized definition.

### Zone start (%)

An individual stat: the share of a player's shifts that begin in the offensive zone, out of shifts that begin in the offensive or defensive zone. Only **faceoff-anchored** shift starts count — a shift beginning on the fly (no faceoff) or at a neutral-zone faceoff is excluded from the denominator entirely, never bucketed as a third category. Home-team players by default; opposing-team players once `opponent_shifts_complete` is set for that game.

### Plus-minus (+/-)

An individual stat: on-ice goals-for minus goals-against, strictly at whichever `strength_state` filter is active (default 5v5) — no special-case exclusion of power-play goals the way the classic NHL stat has. The classic number is simply what you get by using the 5v5 default; "all situations" is an intentionally different variant, not a patched-up classic +/-. Home-team players by default; opposing-team players once `opponent_shifts_complete` is set for that game.

### Game

A single tracked hockey game, identified by an internal ID. Carries `opponent_shifts_complete` (see below), and — settled while designing [League API integration contract](.scratch/hockey-analyzer-spec/issues/11-league-api-integration-contract.md) — an optional `league_id` (the league site's numeric game ID, populated when the game is created from a league game link; used to detect and refuse a duplicate import of the same game), `date`, `venue`, and final + period-by-period score. Score has no stats-engine consumer (goal counts for stats come from tagged `shot_attempt` events, not this field) — it's kept purely as game-list context and as a manual sanity-check against the tagger's own goal count once tagging is done. Fields like attendance, referees, and linesmen, though scrapable from the league site, are deliberately not persisted — nothing in this spec consumes them. A game created from a league link is expected to already be finished — the league site's pre-game state (empty score, no lineups yet) is a real state ticket 11 chose not to support, since game creation only happens once footage exists to tag against.

A `Game` also carries `home_team_id`/`away_team_id` — nullable until each side's `Team` is picked (mirroring the manual setup flow's progressive team selection, one side at a time), and, unlike `rink_type`, freely correctable afterward: nothing is stored *relative to* home/away the way **Rink coordinates** are stored relative to rink geometry, so fixing a wrong pick later never silently reinterprets anything already tagged. The two must never be the same `Team`. This completes a "home/away" concept the schema already half-committed to via `home_score`/`away_score` existing with no team reference to anchor them.

`video_path` is the absolute path to the game's footage file. It's attached on demand — the first time footage is opened while that game is active — rather than required at game creation, since a game record can exist before footage is even exported; once attached, it's what lets reopening a game later load its footage automatically instead of re-browsing for the file. A missing file at resume time is a relink, not something this field tries to solve portably (e.g. across machines or moved drives).

`updated_at` tracks the most recent tagging activity for the game, not just edits to the `Game` row itself — it's bumped by any `Event`, **Game roster entry**, or **Game unit assignment** logged against it too, since that's the overwhelmingly dominant activity once a game exists. It drives the "resume a game" picker's default ordering (most recently worked on first), on the premise that recency is the axis someone hunting for "the game I was just tagging" is almost always scanning by.

### Opponent shifts complete

A per-`Game` boolean (`opponent_shifts_complete`, default `false`) recording whether the visiting team's `shift_change` events were tagged completely enough to trust on-ice attribution for their players in that game. The home team's shift tracking is always assumed complete. This flag gates every on-ice-attribution stat (individual Corsi/Fenwick, +/-, zone starts) for the opposing team's players — team-wide tallies (team Corsi/Fenwick/PDO) never need it, since they don't require knowing on-ice sets at all. When a multi-game stat selection mixes flagged and unflagged games for a team, the individual stat is computed only over the flagged-complete subset, and the result reports which games were included/excluded rather than silently narrowing. A per-game flag rather than a home/away rule, so individual stat eligibility tracks actual tagging completeness rather than team identity — see [ADR-0002](docs/adr/0002-opponent-shift-completeness-flag.md).

### Position

A fixed attribute of a `Player` (`C`/`LW`/`RW`/`D`/`G`), set once, not per-game — unlike jersey number, a player's position doesn't vary from game to game for stats purposes. Identifies goalies (for goalie stats) and enables free per-position rollups of any individual stat (e.g. average Corsi among defensemen).

### Game unit assignment

A player's nominal line/pairing for one game: `(game_id, player_id, team_id, unit_type, unit_number)`, where `unit_type` is `forward-line`/`defense-pair`/`power-play`/`penalty-kill`. Distinct from **Game roster entry** because a player can hold *several* unit assignments in the same game at once (e.g. Forward-Line 1 *and* Power-Play 1 *and* Penalty-Kill 2 simultaneously) — at most one assignment per `(game, player, unit_type)`. Created only for the unit types relevant to that player (a player who never plays special teams has no `power-play`/`penalty-kill` row), same on-demand philosophy as **Game roster entry**. A **line stat** (e.g. "Forward-Line 1's Corsi") means the shot attempts occurring during the intersection of all that unit's members' on-ice intervals — full-unit simultaneous overlap only; if one member is swapped out mid-shift, that ice time drops out of the line stat entirely. Its strength-state filter defaults to the unit's natural context (forward-line/defense-pair → 5v5, power-play → PP states, penalty-kill → SH states), overridable. Mid-game line reshuffles aren't representable in v1 — a player's unit assignment is fixed for the whole game, even though actual deployment (which players are really on the ice together at any moment) is unconstrained and can differ from the nominal assignment.

Tagging note: a "line change" (a whole unit coming on/off together) is a tagging-UI convenience only — it bulk-creates the individual `shift_change` events for that unit's members in one action. It is not a stored event type and leaves ticket 03's event schema untouched.

### Goalie stats (SV%, GAA, HD SV%, xGA)

Individual goalie stats, identified via `Position: G` and tracked on/off ice via the ordinary `shift_change` event (no separate event type). **SV%** = saves / shots-on-goal-faced while that goalie is in net. **GAA** = (goals-against while in net) × 60 / minutes played, where minutes played uses derived **game clock** time (video time minus stoppage gaps within the goalie's on-ice interval), not raw video-elapsed time. Both default to an **all-situations** strength filter (5v5 available as an override) — unlike every other stat in this glossary, which defaults to 5v5 — because that's how SV%/GAA are conventionally reported. **HD SV%** (high-danger save percentage) = saves / shots-on-goal-faced restricted to shots where `shot_attempt.high_danger` is true — see **Danger zone**. **xGA** = sum of `shot_attempt.xg` (see **xG (expected goals)**) for shots against while that goalie was in net, at the active strength filter — the formula is defined now, but isn't computable until the xG model itself exists (deferred per the map's Notes, same as RAPM).

### Stint (RAPM data requirement)

The unit of observation RAPM needs: a video-timestamp interval during which the combined on-ice skater set for *both* teams, and the active `strength_state`, are both constant. Never stored — computed on demand from existing `shift_change` events and each event's `strength_state` field, the same "derived, not stored" treatment already given to **Shift**. A new stint begins at *either* a combined-roster change on either team *or* a `strength_state` change, whichever comes first, since letting strength state drift mid-stint (e.g. a delayed-penalty tag arriving without an accompanying `shift_change`) would mix two different game states into one regression row. A stint's duration is measured as derived **game-clock** elapsed time (stoppage gaps excluded), not raw video-elapsed time, matching the precedent **Goalie stats** set for minutes played — dead time inside a mid-stint whistle isn't ice time and would otherwise understate the stint's actual pace of play. RAPM's target metric is shot-attempt differential (`shot_attempt` events occurring within the stint) rather than goal differential, since goals are too sparse per stint to regress on; this needs no schema beyond what's already defined for **Shot attempt**. Opponent-team stints reuse **Opponent shifts complete** as-is — no separate completeness flag — since the data shape (on-ice sets + shot attempts per stint) is identical to Corsi's. Additional regression covariates (score-state, home/road) are deliberately left unspecified here: both are reconstructable later from the goal-event timeline and `Team.is_user_team`, and pinning them down now would risk capturing fields the eventual regression design never uses.

### Clip

A single exported video segment covering one `Event`'s **padding window** (see below), encoded H.264/AAC in an `.mp4` container regardless of the source footage's own format — chosen as the one combination that plays natively on WhatsApp, phone Mail apps, and both mobile OSes without the recipient needing the hockey-analyzer app or any transcoding on their end. A clip export always draws its events from a single game's footage file — never spanning multiple games — since footage files aren't guaranteed to share resolution/codec, and reconciling that is deferred past v1.

### Padding window

The buffer of footage included before and after an `Event`'s video timestamp when generating a **Clip**. Set once per export (a single before/after value applied to every clip that export produces) rather than per individual clip — tunable per export, but not worth a per-clip review step for the marginal gain, since events of the same type need roughly the same lead-in/lead-out. Defaults to a fixed value when the user doesn't override it.

### Highlight reel

The alternative output shape for a clip export: instead of one file per selected `Event`, all selected clips are concatenated into a single video file. Chosen per export, not a global setting — the same filtered selection of events can be exported either way depending on who it's going to (e.g. individual clips to text one player, a reel to send the whole team).

### Report bundle

A self-contained, versioned export artifact — a zip archive with a dedicated extension — capturing a frozen snapshot of a single game, team, or player report: computed stat outputs, baked chart images (see **Report bundle chart**), a sender-authored written summary (Markdown, frozen at export), and, for team/player reports, the list of games the aggregation drew from. Carries no raw events or shifts, only the numbers the sender's report view was already showing at export time — a bundle is a fixed artifact, not a live data-exploration surface, so re-filtering or recomputing from an opened bundle isn't possible. Each stat group (skater stats, goalie stats) freezes whichever strength-state filter was active for it individually in the live app, mirroring **Strength state**'s existing per-computation configurability rather than collapsing the whole report to one filter. A stat affected by **Opponent shifts complete** or an **Unknown player reference** is shown with a visible caveat, never silently dropped, matching those terms' existing "narrows, never silently" precedent. Openable on any independent install of the app (no server, no shared database) — see **Report bundle identity** for how player/team references resolve across installs. Versioned via a single integer schema version in the bundle's manifest, bumped on any breaking field change; an app refuses to open a bundle whose version is newer than it understands, and best-effort opens an older one, treating fields it doesn't recognize as absent.

### Report bundle identity

How player/team references resolve when a report bundle is opened on a different install than the one that generated it. `Team` carries an optional `league_id` (populated from a league game-link import — see **Team**), and a bundle uses it when present, falling back to name otherwise. `Player` has no cross-install identifier at all — per the [league API investigation](.scratch/hockey-analyzer-spec/issues/02-league-api-investigation.md), none exists anywhere in the source data — so a bundle always carries a player as `full_name` + jersey number, the same manual-matching precedent **Player** already uses for cross-game identity within one install, just extended to a second install. There's no automated way to tell that "Player #14 Smith" in a received bundle is the same tracked `Player` record the recipient already has; reconciling that, if wanted, is a manual judgment call for whoever opens the bundle.

### Report bundle chart

A chart inside a report bundle is a pre-rendered raster image, baked at export time from whatever the sender's live report view was showing. The bundle carries no chart spec or underlying series data, so a recipient's app can't redraw, restyle, or recompute it. If the sender later wants an updated chart (e.g. after tagging more data for the same games), that means generating a brand-new bundle from the live app, not modifying or re-rendering the one already sent — re-rendering is a capability of the live app's report-generation feature, never of the bundle file itself.

### Out of scope (for now)

**Zone entry/exit** (who carried the puck into a zone, carried vs. dumped) is not a modeled event type. None of the currently-scoped stats (Corsi, Fenwick, xG, PDO, zone starts, +/-, RAPM) need it — zone starts only needs the zone of the faceoff that begins a shift, which the `faceoff` event already covers.

**Mid-game line reshuffles** — a coach moving a player between units partway through a game isn't representable; `Game unit assignment` is fixed for the whole game, accepted as a v1 limitation.
