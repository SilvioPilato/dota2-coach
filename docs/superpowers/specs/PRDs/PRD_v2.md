# Dota 2 Personal Carry Coach — PRD v2

## 1. Goals and Non-Goals

### Goals (v1 — implemented)
- Fetch a player's recent match list via OpenDota API (by Steam ID or profile URL)
- Download the `.dem.bz2` replay from Valve CDN for a specified match
- Parse the replay using the odota parser HTTP sidecar to extract carry-relevant metrics
- Detect mistakes and send a compact structured summary to an LLM via LiteLLM
- Support model selection at runtime via `LLM_MODEL` env var or `--model` CLI flag

### Goals (v1.5 — implemented)
- Inject current patch data (item costs, hero stats) from `dotaconstants` into the LLM prompt
- Inject performance benchmarks from OpenDota `/benchmarks` into the LLM prompt
- Add few-shot coaching examples to the system prompt

### Goals (v2 — this version)
- **All 5 roles supported** (pos 1–5): role auto-detected via OpenDota `lane_role` field, with manual override in UI
- **Percentile-based error detection**: replace hardcoded bracket thresholds with global percentile thresholds from OpenDota benchmarks — no bracket-specific constants, no maintenance per patch
- **Role-aware metrics**: each role observes a different set of metrics; shared base rules + per-role overrides
- **Web UI**: FastAPI backend + single-page HTML frontend — match ID input, full report rendered in browser post-analysis
- **Ward rule corrected**: ward purchases flagged as negative only for pos 1 during laning phase (<15 min); pos 2 ward rule removed entirely; pos 4/5 ward purchases flagged as positive KPI

### Non-Goals (v2)
- No per-tick positional analysis (requires Clarity; deferred to v3)
- No win/loss prediction or hero draft analysis
- No database or match history persistence — stateless, one match per run
- No bracket-filtered benchmarks (uses global percentiles; bracket-specific via STRATZ deferred to v3)
- No SaaS features (auth, multi-user, billing)

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│              Web UI  (FastAPI + single-page HTML)                   │
│   Match ID / Steam URL input → full report rendered in browser      │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ POST /analyze {match_id, player_id}
┌───────────────────────────▼─────────────────────────────────────────┐
│                     FastAPI backend (api.py)                        │
│   Orchestrates pipeline, returns JSON report to frontend            │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                ┌───────────▼───────────┐
                │   OpenDota API        │  GET /matches/{match_id}
                │   (httpx async)       │  GET /players/{account_id}/recentMatches
                └───────────┬───────────┘
                            │ match metadata + replay URL
                ┌───────────▼───────────┐
                │  Valve CDN            │  GET replay URL (.dem.bz2)
                │  Availability Check   │  → 404 = expired, abort with clear error
                └───────────┬───────────┘
                            │ .dem.bz2 bytes (streamed to /tmp)
                ┌───────────▼───────────┐
                │  odota parser         │  POST /upload (multipart .dem.bz2)
                │  (Docker sidecar      │  ← returns full JSON event log
                │   localhost:5600)     │
                └───────────┬───────────┘
                            │ raw parser JSON (potentially 100KB+)
                ┌───────────▼───────────┐
                │  Pydantic Extractor   │  Maps raw JSON → MatchMetrics model
                │                       │  Discards everything irrelevant
                └───────────┬───────────┘
                            │ MatchMetrics (typed, compact)
                ┌───────────▼───────────┐
                │  Role Detector        │  lane_role → RoleProfile (pos 1–5)
                │  (role.py)            │  Selects metric set + percentile rules
                └───────────┬───────────┘
                            │ MatchMetrics + RoleProfile
                ┌───────────▼───────────┐
                │  Error Detector       │  Applies percentile thresholds
                │  (detector.py)        │  pct < 0.20 → critical
                │                       │  pct < 0.35 → high
                │                       │  pct < 0.45 → medium
                └───────────┬───────────┘
                            │ MatchMetrics + []DetectedError
                ┌─────┬─────▼──────────────────────────────┐
                │NEW  │  Context Enricher (enricher.py)     │
                │v1.5 │  ├─ OpenDota /benchmarks → bracket  │
                │     │  │   GPM/LH/XPM averages for hero   │
                │     │  └─ dotaconstants → patch item costs │
                └─────┴─────┬──────────────────────────────┘
                            │ MatchMetrics + []DetectedError + EnrichmentContext
                ┌───────────▼───────────┐
                │  Prompt Builder       │  Serializes to <900 token user message
                └───────────┬───────────┘
                            │ system prompt + user message
                ┌───────────▼───────────┐
                │  LiteLLM              │  litellm.completion(model=LLM_MODEL, ...)
                │  (model via env/flag) │
                └───────────┬───────────┘
                            │ LLM response text
                ┌───────────▼───────────┐
                │  Rich Terminal Output │  Renders coaching report with panels
                └───────────────────────┘
```

**Rate limit strategy:** v1 makes at most 2 OpenDota API calls per run. v1.5 adds up to 2 more (benchmarks + constants) but caches both for 1 hour on disk — subsequent runs within the hour cost zero extra calls. At 60 req/min unauthenticated this is never a concern.

---

## 3. Data Model

The odota parser returns a large JSON with raw event streams. The extractor discards everything except the fields below. All times are in minutes unless noted.

```python
class LHEntry(BaseModel):
    minute: int
    last_hits: int        # cumulative LH at this minute
    denies: int

class DeathEvent(BaseModel):
    time_minutes: float   # when the death occurred
    killer: str           # hero name or "creeps"

class TeamfightEntry(BaseModel):
    start_time_minutes: float
    participated: bool    # did the carry engage
    damage_dealt: int
    deaths: int

class MatchMetrics(BaseModel):
    match_id: int
    hero: str
    duration_minutes: float
    result: Literal["win", "loss"]

    # Laning
    lh_at_10: int                     # last hits at 10:00
    denies_at_10: int
    deaths_before_10: int
    death_timestamps_laning: list[float]   # death times < 10 min
    net_worth_at_10: int              # carry's net worth
    enemy_carry_net_worth_at_10: int  # pos 1 on opposing team
    net_worth_at_20: int
    enemy_carry_net_worth_at_20: int

    # Farming
    gpm: int                          # match GPM
    xpm: int                          # match XPM
    first_core_item_minute: float     # minutes to first item flagged as core
    first_core_item_name: str

    # Positioning
    laning_heatmap_own_half_pct: float    # % of positions 0–10 min in own half of map
    ward_purchases: int                    # number of observer/sentry wards bought by carry

    # Fighting
    teamfight_participation_rate: float   # participated / total teamfights
    teamfight_avg_damage_contribution: float  # avg % of team damage in fights

    # Objectives
    first_roshan_minute: float | None     # None if Roshan not taken
    first_tower_minute: float | None      # first tower taken by either team
```

**Why these fields:**
- `lh_at_10` and `net_worth_delta` are the most reliable laning indicators at Crusader/Archon
- Death timestamps let us distinguish "died once late in lane" vs "died at 3min feeding"
- `first_core_item_minute` is the clearest proxy for farm efficiency without replaying itemization logic
- `laning_heatmap_own_half_pct` detects passivity without per-tick precision
- `ward_purchases` catches the bad habit of carry buying wards instead of farm items
- Teamfight participation catches split-pushers who farm while team wipes

**Fields not extracted (and why):**
- Raw gold/XP graphs: too verbose; `gpm`, `xpm`, and net worth deltas are sufficient
- Individual ability usage: noisy, hero-dependent, deferred to v2
- Courier usage timeline: low signal at this bracket

---

## 4. Error Detection Logic

### 4.1 Design principle: percentile-based, not bracket-specific

All numeric thresholds are replaced with **global percentile thresholds** sourced from OpenDota `/benchmarks`. This eliminates hardcoded bracket constants and makes the system self-updating as the meta changes — the benchmarks reflect the current patch automatically.

```python
class DetectedError(BaseModel):
    category: str
    description: str
    severity: Literal["critical", "high", "medium"]
    metric_value: str    # e.g. "32 LH at 10:00"
    player_pct: float    # e.g. 0.23 = 23rd percentile globally on this hero
    context: str         # e.g. "global median for Anti-Mage is 48 LH at 10"

SEVERITY_THRESHOLDS = {
    "critical": 0.20,   # below 20th percentile globally on this hero
    "high":     0.35,
    "medium":   0.45,
}
```

Severity is assigned automatically: `if player_pct < 0.20 → critical`, etc. No per-bracket tuning needed.

### 4.2 Shared base rules (all roles)

These rules apply regardless of position. Conditions that are not metric-driven (deaths, ward rule) retain simple logic but with role-specific parameters.

| # | Category | Condition | Roles |
|---|----------|-----------|-------|
| B1 | Unsafe laning | `deaths_before_10 > role_death_limit` | all — limit varies by role (see 4.3) |
| B2 | Very early death | `deaths_before_10 >= 1 and death_timestamps[0] < 5.0` | all |
| B3 | Farming during fights | `teamfight_participation_pct < role_tf_limit` | all — limit varies by role |
| B4 | Dying in fights | `teamfight_avg_damage_pct < 0.10 and fight_deaths > 1` | pos 1–3 only |

### 4.3 Role profiles

Each role has a `RoleProfile` defining which metrics to observe and role-specific non-percentile limits.

```python
ROLE_PROFILES = {
    1: RoleProfile(
        observed_metrics=["gpm", "lh_at_10", "first_core_minute",
                          "net_worth_delta_10", "net_worth_delta_20",
                          "laning_heatmap_own_half_pct", "teamfight_participation"],
        death_limit_before_10=2,
        tf_participation_limit=0.40,
        ward_rule="flag_if_laning_phase",   # wards bought before 15min = bad habit
    ),
    2: RoleProfile(
        observed_metrics=["gpm", "lh_at_10", "first_core_minute",
                          "rune_control_pct", "tower_damage",
                          "teamfight_participation"],
        death_limit_before_10=1,            # mid dying = free roam for enemy
        tf_participation_limit=0.45,
        ward_rule="none",                   # sentry/obs for rune/deward is correct
    ),
    3: RoleProfile(
        observed_metrics=["gpm", "lh_at_10", "first_core_minute",
                          "stacks_created", "stun_time",
                          "teamfight_participation", "initiation_rate"],
        death_limit_before_10=3,
        tf_participation_limit=0.50,
        ward_rule="none",
    ),
    4: RoleProfile(
        observed_metrics=["stacks_created", "ward_placements", "deward_pct",
                          "stun_time", "hero_healing", "teamfight_participation"],
        death_limit_before_10=3,
        tf_participation_limit=0.55,
        ward_rule="require_minimum",        # < 8 wards placed = underperforming
    ),
    5: RoleProfile(
        observed_metrics=["ward_placements", "deward_pct", "stacks_created",
                          "stun_time", "hero_healing", "teamfight_participation"],
        death_limit_before_10=3,
        tf_participation_limit=0.55,
        ward_rule="require_minimum",        # < 10 wards placed = underperforming
    ),
}
```

### 4.4 Percentile lookup

For each metric in `RoleProfile.observed_metrics`, the detector:
1. Gets `player_value` from `MatchMetrics`
2. Gets `player_pct` from `EnrichmentContext.benchmarks` (already fetched by enricher)
3. Applies `SEVERITY_THRESHOLDS` to assign severity
4. Skips the metric if OpenDota does not expose a benchmark for it (e.g. `stun_time`) — falls back to absolute comparison with a role-specific default

Top 3 errors by severity are passed to the LLM. If fewer than 3, all are passed.

### 4.5 Future upgrade path (v3)

Replace global percentiles with bracket-filtered percentiles via STRATZ GraphQL API (`heroStats` filtered by `bracketBasicIds`). The `EnrichmentContext` model already has a `bracket_source` field reserved for this — switching is a one-line change in `enricher.py`.

---

## 5. LLM Prompt Design

### System Prompt (~120 tokens, role-aware)

The system prompt is assembled dynamically in `prompt.py` — the role name is injected so the LLM reasons with the correct frame:

```
You are a concise Dota 2 coach reviewing a {role_label} (position {role}) performance.
Analyze the metrics and percentile rankings provided. Identify the 3 most impactful
mistakes, ranked by how much they cost the player. Be direct. Use Dota terminology.
Give specific, actionable advice — not generic tips.

Format your response exactly as:

MISTAKE 1 (Critical/High/Medium): [what went wrong]
→ Fix: [one concrete action for next game]

MISTAKE 2 ...
MISTAKE 3 ...

PRIORITY FOCUS: [single most important habit to change]
```

`role_label` is one of: "carry", "mid", "offlaner", "soft support", "hard support".

### User Message Template (role-adaptive, ~350–550 tokens)

The user message renders only the metrics relevant to the detected role. Common header + role-specific body + shared enrichment block:

```
Match: {hero} | pos {role} ({role_label}) | {result} | {duration_minutes:.0f} min

PERFORMANCE (percentiles are global, all brackets, this hero):
{role_specific_metrics_block}

TEAMFIGHTS:
- Participation: {teamfight_participation:.0%} ({teamfight_pct:.0f}th pct)
- Deaths before 10 min: {deaths_before_10}{death_detail}

PATCH CONTEXT ({patch_version}):
{patch_context_block}

DETECTED ISSUES (auto-flagged, top 3 by severity):
{detected_errors_list}
```

**Role-specific metrics blocks** (only the relevant block is included):

```
# Pos 1 / Pos 2 block:
- GPM: {gpm} ({gpm_pct:.0f}th pct, global median {gpm_median})
- LH at 10 min: {lh_at_10} ({lh_pct:.0f}th pct, global median {lh_median})
- First core: {first_core_item_name} at {first_core_minute:.1f} min
- Net worth delta at 10: {nw_delta_10:+d}g vs opposing pos 1
- Net worth delta at 20: {nw_delta_20:+d}g vs opposing pos 1

# Pos 3 block:
- GPM: {gpm} ({gpm_pct:.0f}th pct)
- LH at 10 min: {lh_at_10} ({lh_pct:.0f}th pct)
- Stacks created: {stacks_created}
- Stun time dealt: {stun_time:.1f}s

# Pos 4 / Pos 5 block:
- Ward placements: {ward_placements} ({ward_pct:.0f}th pct)
- Deward efficiency: {deward_pct:.0%}
- Stacks created: {stacks_created}
- Stun time dealt: {stun_time:.1f}s
- Hero healing: {hero_healing}
```

`{detected_errors_list}` renders each error as `- [{severity}] {description} — {metric_value} ({player_pct:.0f}th pct)`.

### Token Budget

| Component | Est. tokens |
|-----------|-------------|
| System prompt (role-aware) | ~120 |
| Few-shot examples (1 per role × 5 roles) | ~400 |
| User message (role-specific metrics) | ~250–400 |
| Patch context block | ~100–150 |
| LLM response | ~200–300 |
| **Total** | **~1070–1370** |

Target is under 1200 input tokens. Few-shot examples are hardcoded in `prompt.py` — one per role, ~80 tokens each. Only the example matching the current role is injected (not all 5), keeping the budget flat regardless of role.

---

## 6. odota Parser Integration

### Running the Sidecar

```bash
docker run -d \
  --name odota-parser \
  -p 5600:5600 \
  odota/parser
```

The parser exposes a single endpoint: `POST /` (not `/upload` — the actual odota parser accepts the replay as a streamed body). The container is stateless; it parses one replay per request and returns JSON.

**Health check before use:**
```
GET http://localhost:5600/health  →  200 OK
```
If the health check fails, the CLI exits with a clear error: `"odota parser is not running. Start it with: docker run -d -p 5600:5600 odota/parser"`.

### Calling the Parser from Python

```python
async def parse_replay(dem_path: Path) -> dict:
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(dem_path, "rb") as f:
            response = await client.post(
                "http://localhost:5600",
                content=f,
                headers={"Content-Type": "application/octet-stream"},
            )
        response.raise_for_status()
        return response.json()
```

Timeout is 120 seconds — parsing a 45-minute replay typically takes 10–30 seconds locally.

### Replay Expiry Handling

Valve CDN replays expire after approximately 7–10 days. The flow is:

1. Fetch match metadata from OpenDota: `GET /matches/{match_id}` — includes `replay_url` field
2. If `replay_url` is `null` or missing → exit immediately: `"Replay not available. Valve CDN replays expire after ~7 days. This match is too old."`
3. If `replay_url` is present, `HEAD` the URL before downloading:
   - `200` → proceed with download
   - `404` / `403` → exit: `"Replay has expired on Valve CDN. Match too old to analyze."`
   - Other errors → exit with the HTTP status and URL for debugging
4. Download streams the `.dem.bz2` directly into a `tempfile.TemporaryDirectory()` used as a context manager — Python cleans it up automatically on exit, including on SIGINT (Ctrl+C). Do not use `NamedTemporaryFile` + manual `finally` — the context manager is safer and handles signals correctly.
5. Both `.dem.bz2` and decompressed `.dem` live inside the temp dir and are removed when the context exits.

**No local caching of replays in v1.** Each run re-downloads and re-parses. If replay caching is needed, it becomes a v2 feature alongside match history persistence.

---

## 7. Open Questions

These must be decided before implementation begins:

| # | Question | Impact |
|---|----------|--------|
| 1 | **How does the user specify which match to analyze?** Options: (a) `--match <match_id>` explicitly, (b) `--player <steam_id>` to auto-pick the most recent match, (c) both. Recommendation: support both, default to most recent match when only `--player` is given. | CLI design, OpenDota call count |
| 2 | **What counts as "first core item"?** ~~We need a curated list~~ **Resolved** — see Appendix A. | Error detector accuracy |
| 3 | **How does the odota parser expose laning heatmap data?** The parser JSON structure for positional data needs to be verified against a real match before building the extractor. If it only returns zone-bucketed presence rather than coordinate arrays, `laning_heatmap_own_half_pct` calculation will differ. | Data model implementation |
| 4 | **How does the odota parser identify each player's hero?** We need to reliably select the player's own hero from the parser output, which may index by player slot (0–9) or by Steam ID. The OpenDota match metadata includes `player_slot` which maps to parser output. This mapping needs to be confirmed. | Extractor correctness |
| 5 | **What is the enemy carry identification heuristic?** OpenDota match data includes `lane_role` per player. `lane_role == 1` = safe lane. We identify the enemy safe lane player as the opposing carry. But in some matches, the "carry" plays offlane or mid. For v1, use `lane_role == 1` on the opposing team; flag as a known limitation. | Net worth delta accuracy |
| 6 | **LLM API key management:** The tool needs at least one provider key (Anthropic or OpenAI) to function. Decision needed: require the user to set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` themselves (no key management in the tool), or support a `.env` file loaded at startup via `python-dotenv`. Recommendation: load `.env` automatically if present, but document that keys must be user-supplied. | Onboarding UX |
| 7 | **What happens if the parser returns no teamfight data?** Some short matches or stomp games may have zero detected teamfights. The error detector and prompt builder must handle `teamfight_participation_rate = None` gracefully (skip that error rule, omit from prompt). | Edge case handling |

---

## 8. v1.5 Additions

### 8.1 Bracket Benchmarks (enricher.py)

OpenDota exposes per-hero performance benchmarks via:

```
GET https://api.opendota.com/api/benchmarks?hero_id={hero_id}
```

Response includes percentile distributions for `gold_per_min`, `xp_per_min`, `last_hits_per_min`, `hero_damage_per_min`, and `kills_per_min`. The `pct` field per metric tells us what percentile the player's value fell in against all recent matches on that hero.

What we extract into `EnrichmentContext`:

```python
class HeroBenchmark(BaseModel):
    metric: str          # e.g. "gold_per_min"
    player_value: float
    player_pct: float    # 0.0–1.0, percentile vs all players on this hero
    bracket_avg: float   # derived: value at ~0.5 pct for Crusader/Archon

class EnrichmentContext(BaseModel):
    patch_name: str                        # e.g. "7.38c"
    benchmarks: list[HeroBenchmark]        # GPM, XPM, LH/min for played hero
    item_costs: dict[str, int]             # item_name → current gold cost
    hero_base_stats: dict[str, float]      # base_damage, base_armor, move_speed
```

**Caching:** benchmark data is stable for hours; cache to `~/.dota_coach/benchmarks_{hero_id}.json` with a 6-hour TTL. Item/hero data from dotaconstants is cached indefinitely until the package version changes — check `dotaconstants.__version__` on startup and invalidate if different.

**Rate limit impact:** adds 1 extra OpenDota API call per run (benchmarks endpoint). Total calls per run: 3 (match metadata + benchmarks + optional player lookup). Still well within 60 req/min.

### 8.2 Patch Data (enricher.py)

**Source:** `odota/dotaconstants` — a maintained JSON dataset that mirrors Valve's game files and is updated on every patch. Available as a Python-importable package or via raw GitHub fetch:

```
https://raw.githubusercontent.com/odota/dotaconstants/master/build/items.json
https://raw.githubusercontent.com/odota/dotaconstants/master/build/heroes.json
```

Install as a dependency: `pip install dotaconstants` (Python port) or fetch the JSON directly via httpx at startup and cache locally.

**What we extract per match** (filtered to only relevant data):

```python
# From items.json — only items the player purchased
item_costs = {
    "battle_fury": items["item_battle_fury"]["cost"],  # e.g. 4600
    "power_treads": items["item_power_treads"]["cost"],
    # ... only items in this match's purchase log
}

# From heroes.json — only the played hero
hero_stats = {
    "base_attack_min": hero["base_attack_min"],
    "base_attack_max": hero["base_attack_max"],
    "move_speed": hero["move_speed"],
    "attack_range": hero["attack_range"],
}
```

**Why filtered:** the full items.json is ~500KB. Sending it all to the LLM would blow the token budget and add noise. We extract only what's relevant to the match being analyzed.

### 8.3 Few-Shot Coaching Examples (prompt.py)

Three hardcoded examples added to the system prompt to anchor output quality. These are manually written once and never change at runtime:

```
EXAMPLE 1 (Anti-Mage, loss, poor laning):
Match: Anti-Mage | loss | 38 min
LH@10: 32 | Deaths before 10: 3 (at 4.2, 7.1, 9.8 min) | GPM: 341
First core: Battle Fury at 22.4 min (target: <18 min)
Net worth delta at 10: -1400g

MISTAKE 1 (Critical): 3 deaths before 10 min, including at 4:12 — that early
suggests you walked up the lane without checking rune or mid position.
→ Fix: Before each CS attempt past the T1, glance at minimap for mid
missing. If missing >15s, hug the safe side of the wave.

MISTAKE 2 (High): Battle Fury at 22:24 is 4+ minutes late for AM. You
likely paused farm to join a fight at ~15min that you shouldn't have.
→ Fix: AM's job before BF is zero. Decline every fight until 18min unless
your base is at risk. Ping "need farm" and keep moving.

MISTAKE 3 (High): 32 LH at 10 min with AM is below the hero's ceiling —
AM has one of the highest base damage values for a carry. This suggests
poor equilibrium management or pulling at the wrong time.
→ Fix: Against a dual lane, pull at 1:20 and again at 3:20 to create a
slow push. Let the wave come to you under T1 where you're safe.

PRIORITY FOCUS: Treat BF timing as a hard constraint, not a guideline.
Every death before 18 min delays BF by ~2 minutes. Deaths are not
neutral — they compound.
```

The examples establish: Dota terminology, specific timestamps, causal reasoning ("likely paused farm to join a fight"), and concrete mechanical fixes. The LLM will pattern-match to this style.

**Token cost of 3 examples:** ~250 tokens. Worth it — this is the single highest-ROI prompt change for quality.

---

## 8. v1.5 Additions — Enricher Detail (enricher.py)

This module is the core addition in v1.5. It runs after the error detector and before the prompt builder, fetching two types of external context that make LLM advice version-accurate and bracket-calibrated.

### 8.1 Pydantic model

```python
class HeroBenchmarks(BaseModel):
    hero_id: int
    hero_name: str
    bracket: int                    # 40–80 = Archon range in OpenDota
    avg_gpm: float
    avg_xpm: float
    avg_lh_10: float                # average last hits at 10 min
    avg_first_core_minute: float    # average first core item timing

class ItemPatchData(BaseModel):
    item_name: str                  # internal key e.g. "item_battle_fury"
    display_name: str
    cost: int                       # current patch cost in gold
    key_stat: str                   # one-line description e.g. "+65 damage, +6 HP regen"

class EnrichmentContext(BaseModel):
    patch_version: str              # e.g. "7.38c"
    hero_benchmarks: HeroBenchmarks
    items: list[ItemPatchData]      # only items the player purchased this match
```

### 8.2 Data sources

**Bracket benchmarks** — OpenDota exposes per-hero, per-bracket aggregated stats:
```
GET https://api.opendota.com/api/benchmarks?hero_id={hero_id}
```
This returns percentile distributions for GPM, XPM, LH, etc. across all skill brackets. Extract the median (50th percentile) values for the bracket matching the player's rank.

**Patch item data** — use the community-maintained `dotaconstants` npm package data, mirrored as JSON at:
```
https://raw.githubusercontent.com/odota/dotaconstants/master/build/items.json
```
This file is updated on every patch by the OpenDota team and contains current costs, stats, and display names for all items. Filter to only the items present in `MatchMetrics.first_core_item_name` and the player's final inventory.

### 8.3 Caching

Both sources change infrequently (benchmarks: weekly, items: per patch ~every 2-4 weeks). Cache both on disk as JSON files with a TTL:

```python
CACHE_DIR = Path.home() / ".dota_coach" / "cache"
BENCHMARKS_TTL = 3600 * 24        # 24 hours
ITEMS_TTL = 3600 * 24 * 7         # 7 days
```

Cache key: `benchmarks_{hero_id}.json`, `items.json`. On cache hit, skip the HTTP call entirely. This means the enricher adds zero latency on repeat runs.

### 8.4 Prompt injection

The enricher output is injected into the user message as a compact block, between DETECTED ISSUES and the end of the message:

```
PATCH CONTEXT ({patch_version}):
- {hero_name}: base damage {base_damage}, Str/Agi/Int gain {stat_gain}
- Battle Fury: {cost}g ({key_stat})
- Manta Style: {cost}g ({key_stat})
[only items purchased this match]

BRACKET BENCHMARKS (Archon median for {hero_name}):
- GPM: {avg_gpm} (yours: {gpm}, delta: {gpm - avg_gpm:+.0f})
- LH at 10: {avg_lh_10} (yours: {lh_at_10}, delta: {lh_at_10 - avg_lh_10:+.0f})
- First core item: {avg_first_core_minute:.1f} min (yours: {first_core_item_minute:.1f} min)
```

This gives the LLM ground truth for current costs and explicit bracket context without any reliance on training data.

### 8.5 Few-shot examples in system prompt

Add 2 coaching examples directly to the system prompt. These are static constants in `prompt.py` — not dynamic, not fetched. They establish tone, specificity, and use of Dota terminology. Example structure:

```
EXAMPLE OUTPUT (Anti-Mage, loss, 38 min):
LH@10: 32 | Deaths before 10: 3 (at 4.2, 7.1, 9.8 min) | GPM: 341
First core: Battle Fury at 22.4 min | Bracket avg: 19.1 min

MISTAKE 1 (Critical): 3 deaths before 10 min including at 4:12 — that
early suggests walking up the lane without checking mid position or rune.
→ Fix: Before each CS attempt past the T1, glance at minimap for mid
missing >15s. If missing, hug the safe side of the wave.

MISTAKE 2 (High): Battle Fury at 22:24 is 4+ minutes late for AM.
You likely paused farm to join a fight at ~15min you shouldn't have.
→ Fix: AM's job before BF is zero. Decline every fight until 18min
unless your base is at risk. Ping "need farm" and keep moving.

MISTAKE 3 (High): 32 LH at 10 with AM is below the hero's ceiling —
AM has high base damage. Suggests poor equilibrium or pulling timing.
→ Fix: Against a dual lane, pull at 1:20 and 3:20 to create slow push.
Let the wave come to you under T1 where you're safe to CS.

PRIORITY FOCUS: Treat BF timing as a hard constraint. Every death before
18 min delays BF by ~2 minutes. Deaths compound.
```

**Token cost of 2 examples:** ~250 tokens. This is the highest-ROI prompt change for output quality — the LLM pattern-matches to the specificity and causal reasoning style.

---

---

## 9. Web UI (v2)

### 9.1 Stack

- **Backend**: FastAPI — single file `api.py`, one endpoint `POST /analyze`
- **Frontend**: single-page HTML served by FastAPI as a static file — no build step, no bundler, vanilla JS
- **Communication**: JSON over HTTP — frontend POSTs match ID, backend returns the full report as a JSON object, frontend renders it

No separate frontend server. `uvicorn api:app` is the only process to run (alongside the odota parser Docker sidecar).

### 9.2 API contract

```
POST /analyze
Body: { "match_id": "7823456789" }  OR  { "player_id": "76561198XXXXXXX" }

Response 200:
{
  "match_id": "7823456789",
  "hero": "Anti-Mage",
  "role": 1,
  "role_label": "carry",
  "result": "loss",
  "duration_minutes": 38,
  "patch": "7.38c",
  "metrics": { ... },           // MatchMetrics fields
  "benchmarks": [ ... ],        // list of HeroBenchmark
  "errors": [ ... ],            // list of DetectedError with player_pct
  "coaching_report": "...",     // raw LLM output string
  "priority_focus": "..."       // extracted from LLM output
}

Response 404: { "error": "Replay expired or match not found" }
Response 422: { "error": "Could not detect player role from match data" }
Response 503: { "error": "odota parser sidecar not running" }
```

### 9.3 Frontend layout

Single HTML page (`static/index.html`) — matches the mockup described in section 10. Sections:

1. **Input bar** — text field for match ID or Steam URL + "Analyze" button
2. **Match header** — hero name, role badge, result badge, duration, patch tag
3. **Metric cards row** — 4 cards with the most important role-specific KPIs
4. **Two-column section** — left: laning/role detail stats; right: percentile benchmark bars
5. **Coaching report** — 3 mistake blocks (severity badge + description + fix arrow)
6. **Priority focus** — highlighted panel in blue

The frontend is role-adaptive: the metric cards and detail stats section render different fields based on `role` in the response. All rendering logic is in plain JS (~150 lines).

### 9.4 Role override

The frontend exposes a small dropdown next to the hero name: "Role detected: Carry (pos 1) ▾". If the user disagrees with the auto-detected role, they can override it and re-run analysis with `POST /analyze` adding `"role_override": 3`. The backend skips the auto-detection and uses the provided role directly.

### 9.5 New modules

- `api.py` — FastAPI app, orchestrates the full pipeline, returns JSON report
- `static/index.html` — single-page frontend, ~200 lines HTML + inline JS
- `static/style.css` — minimal stylesheet, ~100 lines

### 9.6 Running

```bash
# Terminal 1: odota parser sidecar
docker run -d --name odota-parser -p 5600:5600 odota/parser

# Terminal 2: web server
uvicorn api:app --reload --port 8000
# Open http://localhost:8000
```

---

## Appendix A — Core Item List


---

## 10. Turbo Mode Support

### 10.1 Decision

Turbo mode (game_mode == 23) is supported in v2 with **partial benchmark support**. Full percentile benchmarks are not available for Turbo due to API limitations (see below). The system degrades gracefully.

### 10.2 What works in Turbo

- Replay download and parsing via odota parser — works identically, `.dem` format is the same
- Match metadata detection — `game_mode == 23` in OpenDota match metadata identifies Turbo automatically
- LLM coaching — works fully, with Turbo-specific few-shot examples in `prompt.py`
- Patch data from `dotaconstants` — item costs and hero stats are identical between Turbo and ranked
- Absolute metrics (GPM, LH, deaths, item timings) — displayed as-is, without percentile context

### 10.3 What does not work in Turbo

**Percentile benchmarks are unavailable.** The two candidate sources both fail:

- **OpenDota `/benchmarks`** — no `game_mode` filter parameter. Returns global aggregates mixing ranked and Turbo in unknown proportions, making percentiles meaningless for either mode in isolation.
- **STRATZ GraphQL `heroStats`** — supports `gameModeIds` filter but only exposes `matchCount` and `winCount` per hero, not performance distributions (GPM, LH, item timings). Not usable for our percentile system.

### 10.4 UI behavior for Turbo matches

The report renders normally with one difference: the benchmark percentile bars section is replaced by a notice:

```
⚡ Turbo mode — percentile benchmarks not available for this game mode.
   Coaching is based on absolute metrics and Turbo-specific reference values.
```

Absolute metrics are still shown (GPM, LH at 10, deaths, item timing) alongside Turbo-adjusted reference values hardcoded in `prompt.py` (e.g. first core target < 10 min, GPM reference ~700+).

### 10.5 Few-shot examples for Turbo

`prompt.py` includes one dedicated Turbo coaching example per role, with Turbo-calibrated values:
- GPM reference: ~600–900 (vs ~350–500 ranked)
- First core timing: < 10 min (vs < 18 min ranked)
- LH at 10: ~60–80 (vs ~35–50 ranked, due to doubled gold)
- Deaths before 10: same thresholds — dying is equally punishing in Turbo

### 10.6 Upgrade path for v3 — full Turbo benchmark support

When v3 adds SQLite persistence for match history, Turbo benchmark support becomes complete without any external API dependency.

**Approach**: accumulate parsed Turbo match metrics locally. After N matches per hero (target: 30+), compute percentile distributions directly from the local dataset:

```python
# enricher.py v3 addition
def get_turbo_benchmarks(hero_id: int, metric: str) -> TurboBenchmark | None:
    rows = db.query("""
        SELECT {metric} FROM turbo_matches
        WHERE hero_id = ? AND {metric} IS NOT NULL
        ORDER BY analyzed_at DESC LIMIT 500
    """, hero_id)
    if len(rows) < 30:
        return None  # not enough data yet, fall back to no-percentile mode
    values = sorted(r[metric] for r in rows)
    return TurboBenchmark(
        p25=percentile(values, 0.25),
        median=percentile(values, 0.50),
        p75=percentile(values, 0.75),
        sample_size=len(rows),
        source="local"
    )
```

**Why local data is better than OpenDota Explorer for Turbo:**
- OpenDota Explorer (`/explorer` endpoint) *can* compute Turbo percentiles via raw SQL (`game_mode = 23`), but has a 30-second query timeout, no stability guarantee, and returns global distributions that include all playstyles
- Local data is calibrated to your specific hero pool and playstyle
- No external dependency — works offline
- Zero latency (no HTTP call) after the data is accumulated

**Rollout**: the UI shows a progress indicator — "Turbo benchmarks: 12/30 matches needed for Anti-Mage" — until the threshold is reached. Below threshold, falls back to the v2 behavior (absolute metrics, no percentile bars).


---

## 11. Chat Feature (v2)

### 11.1 Design principle

The chat is a stateless follow-up layer on top of the match report. All match context is sent with every request — no server-side session state. The browser holds the conversation history in memory and sends it in full with each turn.

This means:
- Zero additional backend state to manage
- Context resets on page refresh (acceptable for v2 — cross-session history deferred to v3 with DB)
- Token budget grows linearly with conversation length — manageable for casual Q&A

### 11.2 Context structure

Every chat request carries three layers:

```
SYSTEM PROMPT (fixed for the session):
  ├── Coach persona (role-aware, same as coaching report)
  ├── Match metrics block (MatchMetrics fields — same as /analyze)
  ├── Detected errors block (list of DetectedError with pct)
  ├── Enrichment context (patch data, benchmarks)
  └── Match timeline (Level 2 — see 11.4)

HISTORY (grows per turn):
  ├── assistant: [initial coaching report]
  ├── user: [question 1]
  ├── assistant: [answer 1]
  └── ...

USER MESSAGE:
  └── [current question]
```

The initial coaching report is included as the first `assistant` turn so the model can be questioned about its own reasoning.

### 11.3 API endpoint

```python
# api.py
class ChatRequest(BaseModel):
    match_context: MatchReport    # full report from /analyze, held in browser
    history: list[ChatTurn]       # list of {role, content} pairs
    user_message: str

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str

@app.post("/chat")
async def chat(request: ChatRequest):
    messages = build_chat_messages(request)
    return StreamingResponse(
        stream_llm(messages),
        media_type="text/event-stream"
    )
```

`build_chat_messages()` lives in `prompt.py` — assembles system prompt (with timeline) + history + current message.

`stream_llm()` lives in `coach.py` — calls `litellm.completion(stream=True)` and yields SSE chunks.

### 11.4 Match timeline (Level 2)

A new method `build_timeline()` in `extractor.py` constructs a compact chronological event log from the odota parser combat log. This is injected into the chat system prompt as a `MATCH TIMELINE` block — not shown in the UI report.

**Events included:**

| Event type | Source field in odota JSON | Format |
|---|---|---|
| Player deaths | `killed_log` | `{time} — you died to {killer} ({ability})` |
| Enemy carry deaths | `killed_log` | `{time} — enemy carry died to {killer}` |
| Item purchases (player) | `purchase_log` | `{time} — you purchased {item}` |
| Ward placements | `obs_log`, `sen_log` | `{time} — ward placed at {zone}` |
| Rune pickups | `runes_log` | `{time} — {rune} rune picked up` |
| Teamfights | `teamfights` | `{start_time}–{end_time} — teamfight (you: {participated})` |
| Tower kills | `objectives` | `{time} — {tower} destroyed by {team}` |
| Roshan | `objectives` | `{time} — Roshan killed` |

**Format example:**

```
MATCH TIMELINE (Anti-Mage, 38 min):
02:14 — bounty rune picked up
04:12 — you died to Pudge (hook + rot combo, no TP response)
07:05 — you died to Pudge + Lion (mid missing 40s, overextended)
08:41 — enemy carry purchased Hand of Midas
09:48 — you died to Pudge (3rd death, tower dive)
10:02 — tier-1 bottom tower destroyed by Dire
13:58 — you purchased Power Treads
14:32–15:10 — teamfight at Roshan pit (you did not participate)
16:20 — you purchased Battle Fury
22:24 — teamfight mid (you participated, low damage)
...
```

**Token cost**: ~300–600 tokens depending on match length and event density. Added only to chat system prompt, not to the `/analyze` coaching report prompt.

**Availability**: timeline requires a parsed replay. If replay has expired or parsing failed, the `MATCH TIMELINE` block is omitted from the system prompt with a note: `"Timeline not available — replay data not parsed."` Chat still works, but timestamp-specific questions will be answered with limited context.

### 11.5 System prompt for chat

Slightly different from the coaching report prompt — same context, different persona:

```
You are a Dota 2 coach who has just reviewed a {role_label} match for this player.
You have full access to the match data, detected mistakes, and a timeline of key events.
Answer questions about this specific match or about Dota 2 strategy in general.
Be direct and specific. Reference match data and timestamps when relevant.
If asked about something not in the match data, answer from general Dota knowledge
and clearly distinguish it from match-specific observations.

[match metrics block]
[detected errors block]
[patch context block]
[match timeline block]
```

### 11.6 Frontend additions

Two additions to `static/index.html`:

**Chat panel** — rendered below the coaching report after `/analyze` completes:
- Message history area (scrollable, distinct bubbles for user/assistant)
- Text input + send button
- Streaming renders token-by-token via `fetch` + `ReadableStream`
- Loading indicator while streaming

**Context management** — the full `MatchReport` JSON from `/analyze` is stored in a JS variable and attached to every `/chat` POST. History array is appended in-place. On new match analysis, history resets.

### 11.7 Token budget for chat

| Component | Tokens |
|---|---|
| System prompt (coach persona + match context) | ~500 |
| Match timeline | ~300–600 |
| Initial coaching report (first assistant turn) | ~250 |
| Conversation history (per turn, avg) | ~100 |
| Current user message | ~30–80 |
| LLM response | ~150–300 |

After 10 turns: ~2,500–3,500 total input tokens. Still well within any model's context window and cost-effective per session.

**History truncation**: if history exceeds 20 turns, drop the oldest turns while always keeping: system prompt, initial coaching report, and last 10 turns. This keeps the budget bounded without losing critical context.

### 11.8 New modules and changes

**New:**
- `POST /chat` endpoint in `api.py`
- `build_chat_messages()` in `prompt.py`
- `build_timeline()` in `extractor.py`
- `stream_llm()` in `coach.py` (streaming variant of existing `call_llm()`)
- `ChatRequest`, `ChatTurn` models in `models.py`

**Modified:**
- `static/index.html` — chat panel UI
- `api.py` — new endpoint + StreamingResponse import

### 11.9 Upgrade path to v3

When v3 adds SQLite persistence:
- Chat history can be saved per match and restored across sessions
- "Ask about a previous match" becomes possible by loading the stored MatchReport + timeline as context
- No architectural change needed — just load stored context instead of receiving it from the browser

Hardcoded in `detector.py` as `CORE_ITEMS: frozenset[str]`. The odota parser uses internal item name strings (e.g. `"item_battle_fury"`); map display names below to those keys during implementation.

**Included (core carry items):**

| Display name | Internal key |
|---|---|
| Battle Fury | `item_battle_fury` |
| Manta Style | `item_manta` |
| Black King Bar | `item_black_king_bar` |
| Maelstrom | `item_maelstrom` |
| Mjollnir | `item_mjollnir` |
| Desolator | `item_desolator` |
| Butterfly | `item_butterfly` |
| Daedalus | `item_greater_crit` |
| Eye of Skadi | `item_skadi` |
| Sange and Yasha | `item_sange_and_yasha` |
| Yasha (if first item) | `item_yasha` |
| Monkey King Bar | `item_monkey_king_bar` |
| Hand of Midas | `item_hand_of_midas` |
| Drums of Endurance | `item_ancient_janggo` |
| Aghanim's Scepter | `item_ultimate_scepter` |
| Diffusal Blade | `item_diffusal_blade` |
| Radiance | `item_radiance` |
| Armlet of Mordiggian | `item_armlet` |
| Helm of the Dominator | `item_helm_of_the_dominator` |

**Excluded (non-core / starting / utility):**

Bracer, Wraith Band, Null Talisman, Magic Wand, Orb of Corrosion, Phase Boots, Power Treads, Boots of Speed, Quelling Blade, Stout Shield, Iron Branch, Clarity, Tango, Healing Salve, Town Portal Scroll, Wind Lace, Gloves of Haste, Belt of Strength.

**Rule:** `first_core_item` = the earliest purchase timestamp where `item_name in CORE_ITEMS`. If no core item is purchased (e.g. very short stomp), set `first_core_item_minute = None` and skip rule #4 in the error detector.

---

## Appendix B — Project Structure

```
dota_coach/
├── __init__.py
├── api.py           # NEW v2: FastAPI app — POST /analyze, serves static/
├── opendota.py      # OpenDota API client (httpx async)
├── downloader.py    # Valve CDN download + bz2 decompress + TemporaryDirectory
├── parser.py        # odota sidecar HTTP client + health check
├── extractor.py     # raw odota JSON → MatchMetrics (Pydantic)
├── role.py          # NEW v2: lane_role → RoleProfile; role override logic
├── detector.py      # MatchMetrics + RoleProfile + benchmarks → list[DetectedError]
├── enricher.py      # OpenDota benchmarks + dotaconstants → EnrichmentContext
├── prompt.py        # MatchMetrics + DetectedError + EnrichmentContext → LLM messages
├── coach.py         # LiteLLM call → coaching text
└── models.py        # all Pydantic models (MatchMetrics, DetectedError, RoleProfile, ...)

static/
├── index.html       # NEW v2: single-page frontend + chat panel (~300 lines HTML + inline JS)
└── style.css        # NEW v2: minimal stylesheet (~120 lines)

tests/
├── test_extractor.py   # unit tests with fixture JSON from odota parser + timeline builder
├── test_role.py        # NEW v2: role detection + override logic
├── test_detector.py    # percentile threshold tests
├── test_enricher.py    # mock HTTP, assert cache hit/miss logic
├── test_prompt.py      # token budget assertions (coaching: <1200, chat: <4000)
└── test_chat.py        # NEW v2: build_chat_messages structure, history truncation

fixtures/
├── sample_match.json           # real odota parser output for one match
├── sample_benchmarks.json      # cached OpenDota benchmarks response
└── sample_items.json           # cached dotaconstants items.json (trimmed)

~/.dota_coach/cache/            # runtime cache dir (not in repo)
├── benchmarks_{hero_id}.json
└── items.json

pyproject.toml     # deps: httpx, pydantic, litellm, fastapi, uvicorn, python-dotenv
.env.example       # LLM_MODEL=anthropic/claude-sonnet-4-6 + ANTHROPIC_API_KEY=...
README.md
```

**Entry point:** `uvicorn api:app --reload --port 8000` then open `http://localhost:8000`

**CLI still supported:** `python -m dota_coach.cli analyze --match 7812345678` for headless/scripted use.

---

## Appendix C — v3 Feature: OpenDota Fallback for Expired Replays

### Problem

Valve CDN replays expire after ~7 days. Any match older than that returns a 404/403 on the
`.dem.bz2` URL, causing the pipeline to abort with a `ReplayExpiredError` before any analysis
can be done.

### Proposed Solution

When a replay is unavailable, fall back to OpenDota's own parsed match data. OpenDota stores
per-player per-minute arrays (`gold_t`, `lh_t`, `xp_t`) and end-game stats for all matches it
has parsed — independently of Valve CDN availability. This allows ~80% fidelity analysis on
old matches.

### Metric Coverage

| Metric | Source (fallback) | Fidelity |
| --- | --- | --- |
| GPM / XPM | `gold_per_min`, `xp_per_min` | Full |
| LH @ 10 | `lh_t[10]` | Full (if OpenDota parsed) |
| NW @ 10 / NW @ 20 | `gold_t[10]`, `gold_t[20]` | Full (if parsed) |
| Ward placements | `obs_placed + sen_placed` | Full |
| Stacks created | `camps_stacked` | Full |
| Hero healing | `hero_healing` | Full |
| TF participation | `teamfight_participation` | Full |
| Result / duration | `win`, `duration` | Full |
| Deaths before 10 | ✗ only total deaths available | Degraded |
| First core item time | ✗ not reliably available | Missing |
| Laning heatmap | ✗ positional data not in API | Missing |
| Match timeline (chat) | ✗ no event log without `.dem` | Missing |

### Implementation Plan

1. **New function `extract_metrics_from_opendota(our_meta, match_meta)`** in `extractor.py`
   — reads directly from the player dict in `match_meta["players"]`, using `lh_t`, `gold_t`,
   `xp_t` arrays for laning-phase snapshots.

2. **Fallback in `api.py`** — catch `ReplayExpiredError` in the `/analyze` handler and retry
   with the OpenDota extractor instead of aborting. Return the report with a degraded-mode
   warning flag.

3. **`MatchReport` gets a `degraded` field** — `bool`, defaults to `False`. Set to `True` when
   the OpenDota fallback was used.

4. **Frontend banner** — show a non-blocking yellow notice when `degraded === true`:
   *"Replay expired — analysis based on OpenDota data. Some laning metrics may be less precise."*

5. **Graceful metric gaps** — metrics unavailable in fallback mode (`deaths_before_10`,
   `first_core_item_minute`, `laning_heatmap_own_half_pct`, `timeline`) are set to `None`/`""`.
   The detector and prompt builder already handle `None` for optional fields.

### Non-Goals for this Feature

- No request to OpenDota to trigger a re-parse of an expired match (separate concern).
- No caching of the OpenDota fallback data to disk.
- CLI path not updated (fallback is web-UI only for v3).

---

## 13. Replay & Analysis Caching

**Spec:** `docs/superpowers/specs/2026-03-22-replay-caching-design.md`

### 13.1 Problem

Every call to `/analyze` re-downloads the replay (~100–500 MB), re-parses it (CPU-intensive), and re-calls the LLM, even for repeated requests on the same match. A page refresh also loses the rendered result.

### 13.2 Cache layers

Three independent layers, each bypassable by the user:

| Layer | Storage | Key | TTL |
| --- | --- | --- | --- |
| Decompressed `.dem` file | `~/.dota_coach/cache/replay_{match_id}.dem` | match_id | 7 days |
| Full analysis result (JSON) | `~/.dota_coach/cache/analysis_{match_id}_{account_id}_{role}.json` | match_id + account_id + role (all integers) | 7 days |
| Last rendered result | Browser `localStorage` key `dota_analysis_{match_id}_{player_id}` | match_id + player_id | Until user clears storage |

The role in the analysis cache key is the **resolved integer role (1–5)**, so re-analyzing with a role override always writes a separate cache entry.

### 13.3 New module: `dota_coach/cache.py`

Owns all cache file operations. Five functions:

- `read_analysis_cache(match_id, account_id, role) → dict | None` — returns `None` on miss, expiry, or corrupt JSON (logs `WARNING` on `JSONDecodeError`)
- `write_analysis_cache(match_id, account_id, role, data)` — atomic `.tmp → rename`; logs `WARNING` on serialisation failure
- `get_dem_cache_path(match_id) → Path`
- `is_dem_cache_fresh(match_id) → bool`
- `invalidate_dem_cache(match_id)` — no-op if absent

Both write functions call `mkdir(parents=True, exist_ok=True)` on `CACHE_DIR` before use.

### 13.4 `downloader.py` changes

New signature: `download_and_decompress(replay_url, match_id, force_redownload=False)`

- Cache hit → yield cached `.dem` directly (HEAD check skipped)
- Cache miss → download `.bz2` to a `TemporaryDirectory` (auto-cleaned), decompress atomically to `CACHE_DIR`, yield
- `force_redownload=True` → calls `invalidate_dem_cache` first, then re-downloads

### 13.5 `api.py` changes

`AnalyzeRequest` gains two flags:

```python
force_reanalyze: bool = False    # skip analysis cache, re-run LLM (keep .dem)
force_redownload: bool = False   # delete .dem and re-download (implies force_reanalyze)
```

OpenDota fetch + role detection (steps 1–4) always run. After role is resolved, the analysis cache is checked. Steps 5–12 (download, parse, LLM) are skipped on a cache hit.

### 13.6 Frontend changes

- **Three buttons**: *Analyze* (always visible), *Re-analyze* and *Re-download replay* (visible only when a result is rendered)
- On submit: check `localStorage` first and render immediately if a cached result is present
- On successful response: write result to `localStorage`

### 13.7 Non-Goals

- Cache eviction UI (manual deletion via filesystem)
- Multi-user / shared cache
- Redis or any external cache store
- Auto-clearing `localStorage` when server cache is invalidated