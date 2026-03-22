# Spike Notes: odota parser JSON field structure
Match used: `8737708052_803628215.dem` (match ID 8737708052)

---

## Output format — SURPRISE

The parser returns **newline-delimited JSON (NDJSON)**, not a single JSON object.
Each line is one event record. The Python client must NOT call `response.json()` — it must
split by newline and parse each line individually.

```python
records = [json.loads(line) for line in response.text.strip().splitlines() if line.strip()]
```

This match produced **125,384 records**.

---

## Event types inventory

| Type | Count | Purpose |
|------|-------|---------|
| `actions` | 58,902 | Per-tick player actions (APM source, not needed for v1) |
| `interval` | 20,020 | Per-second per-player snapshot — **primary data source** |
| `DOTA_COMBATLOG_DAMAGE` | 18,484 | Damage events |
| `DOTA_COMBATLOG_DEATH` | 2,176 | All unit deaths (heroes + creeps) |
| `DOTA_COMBATLOG_PURCHASE` | 343 | Item purchase events |
| `DOTA_COMBATLOG_GOLD` | 1,678 | Gold gain events |
| `DOTA_COMBATLOG_XP` | 2,585 | XP gain events |
| `DOTA_COMBATLOG_TEAM_BUILDING_KILL` | 24 | Tower/barracks kills |
| `CHAT_MESSAGE_ROSHAN_KILL` | 1 | Roshan kill |
| `obs` / `obs_left` | 20 each | Observer ward placed/removed |
| `sen` / `sen_left` | 37 / 34 | Sentry ward placed/removed |
| `player_slot` | 10 | Maps parser slot (0-9) to game player slot |
| `epilogue` | 1 | End-of-game protobuf metadata (hero names, Steam IDs, winner) |

---

## `interval` records — the primary data source

Emitted once per second per player (10 records/sec). Contains cumulative stats.

**Fields confirmed:**
```json
{
  "time": 600,
  "type": "interval",
  "slot": 7,
  "unit": "CDOTA_Unit_Hero_Naga_Siren",
  "hero_id": 89,
  "gold": 4236,
  "lh": 63,
  "denies": 17,
  "xp": 4931,
  "networth": 4576,
  "level": 9,
  "kills": 2,
  "deaths": 0,
  "assists": 1,
  "teamfight_participation": 0.2727,
  "obs_placed": 0,
  "sen_placed": 0,
  "x": 121.09,
  "y": 123.88,
  "stage": 5
}
```

**Key findings:**
- `lh` and `denies` are **cumulative** — use snapshot at t=600 for 10-min values directly
- `networth` is current net worth (not gold earned) — use at t=600 and t=1200 for deltas
- `teamfight_participation` is a **pre-computed cumulative float (0.0–1.0)** — use final interval value
- `x`, `y` are raw float coordinates in a normalized 0-256 range — **NOT zone-bucketed** (better than expected)
- `obs_placed` and `sen_placed` are cumulative ward placements per player
- No direct GPM/XPM field — must compute (see below)

---

## GPM / XPM computation

Not directly provided. Derive from `interval` records at game end:

```python
# GPM: use final interval networth and game duration
# More accurate: sum all DOTA_COMBATLOG_GOLD events for the player
# Simplest: (final_networth + gold_spent) / duration_minutes
# Practical shortcut: networth at final interval / duration gives approximate GPM
```

Recommended approach: track `networth` at the last `interval` record before game end,
divide by `playbackTime_` from `epilogue` (in seconds → convert to minutes).

---

## Player identification

### `player_slot` records (emitted once at game start):
```
slot key '0' → player_slot 0    (Radiant)
slot key '1' → player_slot 1    (Radiant)
slot key '2' → player_slot 2    (Radiant)
slot key '3' → player_slot 3    (Radiant)
slot key '4' → player_slot 4    (Radiant)
slot key '5' → player_slot 128  (Dire)
slot key '6' → player_slot 129  (Dire)
slot key '7' → player_slot 130  (Dire)
slot key '8' → player_slot 131  (Dire)
slot key '9' → player_slot 132  (Dire)
```

**Parser slot 0-4 = Radiant, 5-9 = Dire.** This matches the OpenDota API's `player_slot` convention
(0-4 Radiant, 128-132 Dire). Cross-reference OpenDota match data's `player_slot` to find our player's
parser `slot` index.

### Hero identification in combat log events:
Combat log events (`DOTA_COMBATLOG_DEATH`, `DOTA_COMBATLOG_PURCHASE`) use `npc_dota_hero_*` names
in `targetname`/`attackername` fields — **not slot numbers**.

Cross-reference: use the `unit` field from `interval` records to build a
`{npc_hero_name → slot}` map at extraction time.

The `unit` field uses the `CDOTA_Unit_Hero_*` class name, while combat logs use `npc_dota_hero_*`.
Example: `CDOTA_Unit_Hero_DrowRanger` ↔ `npc_dota_hero_drow_ranger`.
Conversion: lowercase the class suffix and insert underscores.

---

## Hero deaths

Filter `DOTA_COMBATLOG_DEATH` where `targethero=True` and `targetillusion=False`.

```json
{
  "time": 239,
  "type": "DOTA_COMBATLOG_DEATH",
  "attackername": "npc_dota_hero_naga_siren",
  "targetname": "npc_dota_hero_drow_ranger",
  "targethero": true,
  "targetillusion": false,
  "inflictor": "dota_unknown"
}
```

- `time` is in **seconds**. Divide by 60 for minutes.
- `targetname` identifies the hero who died — map to slot via `unit` → `npc` name cross-reference
- Deaths before 10 min: filter `time < 600`

---

## Item purchases

`DOTA_COMBATLOG_PURCHASE` records:

```json
{
  "time": -90,
  "type": "DOTA_COMBATLOG_PURCHASE",
  "targetname": "npc_dota_hero_drow_ranger",
  "valuename": "item_ward_dispenser"
}
```

- `targetname`: hero who purchased
- `valuename`: internal item name (matches `CORE_ITEMS` frozenset in PRD)
- `time`: seconds since game start (negative = pre-game / during draft)
- Ward purchases: `valuename` in `{"item_ward_dispenser", "item_ward_sentry"}`
- First core item: scan purchases for `valuename in CORE_ITEMS`, take minimum `time > 0`

---

## Positional data (laning heatmap)

Available directly from `interval` records: `x` and `y` float coordinates (0–256 range).

Map geometry in this coordinate system:
- Radiant base ≈ bottom-left (low x, low y)
- Dire base ≈ top-right (high x, high y)
- River / midpoint ≈ x=128, y=128
- Diagonal dividing line: `x + y ≈ 256`

**Radiant "own half"**: `x + y < 256`
**Dire "own half"**: `x + y > 256`

To compute `laning_heatmap_own_half_pct`:
1. Filter `interval` records for target player, `0 <= time <= 600`
2. Determine player team from `player_slot` mapping
3. Count records where position is in own half / total records

No zone-bucketing needed — raw coordinates are available.

---

## Teamfight participation

**Already computed by the parser.** The `teamfight_participation` field in `interval` records is a
cumulative float (0.0–1.0). Use the **last** `interval` record for the target player.

No need to detect individual teamfight events. This simplifies the extractor significantly.

---

## Towers and buildings

`DOTA_COMBATLOG_TEAM_BUILDING_KILL`:

```json
{
  "time": 777,
  "type": "DOTA_COMBATLOG_TEAM_BUILDING_KILL",
  "targetname": "npc_dota_goodguys_tower1_mid"
}
```

- `targetname` contains the building name: `goodguys` = Radiant, `badguys` = Dire
- Filter for `tower` in `targetname` to exclude barracks/ancient
- First tower time: minimum `time` across all tower kill events

---

## Roshan

`CHAT_MESSAGE_ROSHAN_KILL`:

```json
{
  "time": 1470,
  "type": "CHAT_MESSAGE_ROSHAN_KILL",
  "value": 135,
  "player1": 3,
  "player2": 8
}
```

- `time` in seconds
- `player1` is the parser slot of the player who scored the kill

---

## Observer/Sentry wards

`obs` (observer placed) and `sen` (sentry placed):

```json
{
  "time": -39,
  "type": "obs",
  "slot": 9,
  "x": 123.77,
  "y": 131.07
}
```

- `slot`: parser slot of the player who placed it
- Ward purchase count for carry: count `DOTA_COMBATLOG_PURCHASE` where `valuename` in
  `{"item_ward_dispenser", "item_ward_sentry"}` and `targetname` matches our carry's hero

---

## `epilogue` record — match metadata

Single record at `time: -257`. The `key` field is a **JSON string** containing a serialized
protobuf with match summary. Contains:

- `matchId_`: match ID
- `gameWinner_`: 2 = Radiant wins, 3 = Dire wins
- `playbackTime_`: game duration in seconds (e.g., 2190.9)
- `playerInfo_[]`: array of 10 player objects, each with:
  - `heroName_`: **byte array** (not a string) — decode: `bytes(heroName_['bytes']).decode('utf-8')`
  - `steamid_`: Steam ID (integer)
  - `gameTeam_`: 2 = Radiant, 3 = Dire
  - `playerName_`: byte array — same decode method

This is the most convenient source for game winner and player Steam ID → hero mapping.

---

## Summary: what was confirmed vs. what changed

| PRD assumption | Reality |
|----------------|---------|
| Single JSON response | **NDJSON — one event per line** |
| `lh_per_min` table | **`interval.lh` cumulative — snapshot at t=600 directly** |
| Positional heatmap (zone-bucketed?) | **Raw x/y floats in interval records — better** |
| Teamfight summary events | **`interval.teamfight_participation` pre-computed float** |
| GPM/XPM fields | **Not present — derive from networth/duration** |
| Players keyed by slot | **Confirmed: slot 0-9, cross-ref `player_slot` events** |
| Hero names in combat logs | **`npc_dota_hero_*` format — must map to/from `CDOTA_Unit_Hero_*`** |
| Ward purchases | **Confirmed: `DOTA_COMBATLOG_PURCHASE` with `item_ward_dispenser`/`item_ward_sentry`** |
| Item purchase log | **Confirmed: `DOTA_COMBATLOG_PURCHASE` with `valuename` = internal item key** |

---

## Impact on extractor.py design

1. Parse NDJSON, group records by type into lookup structures at load time
2. Build `slot_to_hero` and `hero_to_slot` maps from `interval` records
3. All laning metrics come from a single `interval` snapshot at `time=600`
4. Net worth at 20 min: `interval` snapshot at `time=1200`
5. Teamfight participation: last `interval` record for the player
6. GPM: `networth_final / duration_minutes` (approximate but sufficient)
7. `epilogue` key needs JSON parse + byte array decode for hero/Steam ID mapping
