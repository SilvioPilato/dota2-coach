# Dota 2 Personal Carry Coach — PRD v1

## 1. Goals and Non-Goals

### Goals (v1)
- Fetch a player's recent match list via OpenDota API (by Steam ID or profile URL)
- Download the `.dem.bz2` replay from Valve CDN for a specified match
- Parse the replay using the odota parser HTTP sidecar to extract carry-relevant metrics
- Detect concrete mistakes against opinionated Crusader/Archon carry thresholds
- Send a compact structured summary to an LLM via LiteLLM and display coaching feedback in the terminal
- Support model selection at runtime via `LLM_MODEL` env var or `--model` CLI flag

### Non-Goals (v1)
- No database or match history persistence — stateless, one match per run
- No web UI, Discord bot, or notification system
- No multi-hero or role-agnostic analysis — carry (pos 1) only
- No automated replay download for all recent matches in bulk
- No per-tick positional analysis (requires Clarity; deferred to v2)
- No win/loss prediction or hero draft analysis
- No SaaS features (auth, multi-user, billing)

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLI (Typer + Rich)                         │
│   dota-coach analyze --match <match_id> [--model anthropic/...]     │
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
                │  Error Detector       │  Applies threshold rules
                │                       │  → list of DetectedError objects
                └───────────┬───────────┘
                            │ MatchMetrics + []DetectedError
                ┌───────────▼───────────┐
                │  Prompt Builder       │  Serializes to <800 token user message
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

**Rate limit strategy:** The tool makes at most 2 OpenDota API calls per run (match detail + player lookup if needed). At 60 req/min unauthenticated this is never a concern for single-match analysis. If batch mode is added in v2, add a `asyncio.Semaphore(1)` + 1-second delay between requests.

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

Thresholds are set for Crusader/Archon bracket. Justification: at this MMR, average 10-min LH is ~35–45 (pro baseline ~70), first core item averages 20–22 min (pro baseline 14–16 min), and teamfight participation is often too high (carry farms poorly) or too low (carry dies in fights).

```python
class DetectedError(BaseModel):
    category: str
    description: str
    severity: Literal["critical", "high", "medium"]
    metric_value: str   # human-readable, e.g. "38 LH at 10:00"
    threshold: str      # e.g. "< 45 is poor laning"
```

| # | Category | Condition | Severity | Justification |
|---|----------|-----------|----------|---------------|
| 1 | Poor laning CS | `lh_at_10 < 45` | high | 45 LH = ~1 CS/12s; achievable with basic last-hitting. Below this suggests missing easily contested creeps. |
| 2 | Unsafe laning | `deaths_before_10 > 2` | critical | Two deaths = ~500g+ bounty + 2+ minutes of lane pressure loss. Third death is catastrophic. |
| 3 | Single early death | `deaths_before_10 == 1 and death_timestamps_laning[0] < 5.0` | high | Dying before 5 min usually means overextension or no TP, not a gank — addressable. |
| 4 | Slow core item | `first_core_item_minute > 18` | high | First core (e.g. BF, Midas, Drums+Yasha) beyond 18 min at this bracket indicates inefficient farming patterns or too much fighting. |
| 5 | Net worth deficit at 10 | `(enemy_carry_net_worth_at_10 - net_worth_at_10) > 1000` | high | 1000g deficit at 10 min is approximately one full item component — severe laning loss. |
| 6 | Net worth deficit at 20 | `(enemy_carry_net_worth_at_20 - net_worth_at_20) > 2500` | critical | 2500g at 20 min = ~1 major item behind; generally unrecoverable without team help. |
| 7 | Passive laning | `laning_heatmap_own_half_pct > 0.70` | medium | Spending 70%+ of laning phase on own side of the river means not contesting enemy jungle or pulling. |
| 8 | Carry buying wards | `ward_purchases >= 2` | medium | One ward purchase occasionally happens; 2+ means the carry is filling a support role instead of buying farm items. |
| 9 | Farming during fights | `teamfight_participation_rate < 0.40` | medium | Participating in fewer than 40% of teamfights at this bracket = actively farming while team loses fights. Carry should be present for most major engagements. |
| 10 | Dying in fights | `teamfight_avg_damage_contribution < 0.10 and deaths in fights > 1` | medium | Contributing <10% damage while dying in fights means the carry is jumping in without positioning or spell timing. |

Rules are applied in priority order; the top 3 by severity are passed to the LLM. If fewer than 3 errors are detected, all are passed.

---

## 5. LLM Prompt Design

### System Prompt (fixed, ~100 tokens)

```
You are a concise Dota 2 carry coach reviewing match data for a Crusader/Archon player.
Your job is to identify the 3 most impactful mistakes from the metrics provided, ranked by how much they cost the player.
Be direct. Use Dota terminology. Give specific, actionable advice — not generic tips.
Format your response exactly as:

MISTAKE 1 (Critical/High/Medium): [what went wrong]
→ Fix: [one concrete action for next game]

MISTAKE 2 ...
MISTAKE 3 ...

PRIORITY FOCUS: [single most important habit to change]
```

### User Message Template (~300–600 tokens depending on fight count)

```
Match: {hero} | {result} | {duration_minutes:.0f} min | Match ID: {match_id}

LANING (0–10 min):
- Last hits at 10 min: {lh_at_10} (target: ≥45)
- Denies at 10 min: {denies_at_10}
- Deaths before 10 min: {deaths_before_10}{death_detail}
- Net worth at 10 min: {net_worth_at_10}g (enemy carry: {enemy_carry_net_worth_at_10}g, delta: {delta_10:+d}g)

FARMING:
- GPM: {gpm} | XPM: {xpm}
- Net worth at 20 min: {net_worth_at_20}g (enemy carry: {enemy_carry_net_worth_at_20}g, delta: {delta_20:+d}g)
- First core item: {first_core_item_name} at {first_core_item_minute:.1f} min (target: <18 min)

POSITIONING & HABITS:
- Laning phase own-half positioning: {laning_heatmap_own_half_pct:.0%}
- Ward purchases by carry: {ward_purchases}

TEAMFIGHTS:
- Participation rate: {teamfight_participation_rate:.0%} (target: ≥40%)
- Avg damage contribution: {teamfight_avg_damage_contribution:.0%}

DETECTED ISSUES (auto-flagged):
{detected_errors_list}
```

`{death_detail}` is omitted if 0 deaths, otherwise: ` (at {timestamp} min)` for each death.
`{detected_errors_list}` renders each `DetectedError` as `- [{severity}] {description} — {metric_value}`.

### Token Budget

| Component | Est. tokens |
|-----------|-------------|
| System prompt | ~110 |
| User message | ~280–480 |
| LLM response | ~200–300 |
| **Total** | **~590–890** |

Target is under 800 for the request (input only). The template above stays well within this when teamfight count is ≤ 5. If a match has many teamfights, the summary line format keeps it flat (no per-fight breakdown in the prompt).

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
| 3 | **How does the odota parser expose laning heatmap data?** ⚠️ **Must be validated as a pre-implementation spike** — parse one real match, inspect the raw JSON, confirm the field name and structure before building the extractor. If the parser returns zone-bucketed presence rather than coordinate arrays, `laning_heatmap_own_half_pct` calculation changes. Add this as the first Beads task. | Data model implementation |
| 7 | **What happens if the parser returns no teamfight data?** Some short matches or stomp games may have zero detected teamfights. The error detector and prompt builder must handle `teamfight_participation_rate = None` gracefully (skip that error rule, omit from prompt). | Edge case handling |

---

## Appendix A — Core Item List

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
├── cli.py           # Typer entry point — commands: analyze, recent
├── opendota.py      # OpenDota API client (httpx async)
├── downloader.py    # Valve CDN download + bz2 decompress + TemporaryDirectory
├── parser.py        # odota sidecar HTTP client + health check
├── extractor.py     # raw odota JSON → MatchMetrics (Pydantic)
├── detector.py      # MatchMetrics + CORE_ITEMS → list[DetectedError]
├── prompt.py        # MatchMetrics + list[DetectedError] → LLM messages
├── coach.py         # LiteLLM call → coaching text
└── models.py        # all Pydantic models: MatchMetrics, DetectedError, LHEntry, etc.

tests/
├── test_extractor.py   # unit tests with fixture JSON from odota parser
├── test_detector.py    # threshold rule tests
└── test_prompt.py      # token budget assertions

fixtures/
└── sample_match.json   # real odota parser output for one match (used in tests + spike)

pyproject.toml          # dependencies: httpx, pydantic, litellm, typer, rich, python-dotenv
.env.example            # LLM_MODEL=anthropic/claude-sonnet-4-6 + ANTHROPIC_API_KEY=...
README.md
```

**Entry point:** `dota-coach analyze --match 7812345678` or `dota-coach analyze --player 76561198XXXXXXX`

**Installation:** `pip install -e .` — registers the `dota-coach` CLI command via `pyproject.toml` `[project.scripts]`.