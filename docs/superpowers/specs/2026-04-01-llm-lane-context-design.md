# LLM Lane Matchup & Synergy Context — Design Spec

**Date:** 2026-04-01
**Epic:** dota-analysis-z1l
**Status:** Approved

---

## Problem

The LLM currently receives laning context as a single terse line:

```text
- Lane: Wraith King + Crystal Maiden (synergy +4.2) vs Bristleback (44% WR) + Lion (51% WR) — unfavorable lane
```

This is insufficient for the LLM to reason about laning performance because:

1. **No lane outcome** — NW delta at 10 is in `MatchMetrics` but never shown to the LLM, so it cannot compare expectation vs result.
2. **Synergy not surfaced meaningfully** — synergy scores are embedded in the line but there is no explicit interpretation (good/bad/neutral).
3. **UI gap** — the frontend shows no lane lineup; users cannot see who they laned against without reading the coaching text.

---

## Goals

- Give the LLM an explicit "expected vs actual" framing for the laning phase.
- Surface synergy scores with a clear quality label.
- Show hero chip lineups (with icons) in the match summary card UI.

---

## Out of Scope

- Lane heatmaps or positional data.
- Per-minute CS graphs.

---

## Architecture

No new files. Changes are confined to:

- `dota_coach/models.py` — add `lane_hero_icons: dict[str, str]` to `MatchMetrics`.
- `dota_coach/enricher.py` — populate `lane_hero_icons` during `enrich_lane_matchup()`.
- `dota_coach/prompt.py` — new `_laning_phase_block()` helper, replaces `_lane_line()` call sites.
- `static/index.html` — new lane lineup section in `renderReport()` and associated CSS.

The lane data (`lane_enemies`, `lane_allies`, `lane_ally_synergy_scores`, `lane_matchup_winrates`) is already present in `MatchMetrics` and already serialized via `model_dump()` in the API response.

---

## Backend: `lane_hero_icons` field

A new `lane_hero_icons: dict[str, str] = {}` field is added to `MatchMetrics`. It maps localized hero name → full CDN icon URL.

The `heroes_data` dict (from dotaconstants, already passed into `enrich_lane_matchup`) contains an `icon` field per hero with a relative path like `/apps/dota2/images/dota_react/heroes/icons/antimage.png?`. During enrichment, all heroes in `lane_enemies + lane_allies + [metrics.hero]` are looked up and their icon URLs stored as:

```text
https://cdn.cloudflare.steamstatic.com{icon_path_without_trailing_query}
```

The field is serialized automatically via `model_dump()` — no API changes required.

---

## Backend: `_laning_phase_block()`

### Replaces

`_lane_line()` at two call sites:

- `build_user_message()` — initial coaching prompt
- `_build_chat_system_prompt()` — chat system prompt

### Output format

```text
LANING PHASE:
- Lineup: Wraith King + Crystal Maiden (synergy +4.2) vs Bristleback (44% WR) + Lion (51% WR)
- Avg matchup WR: 47% — slight disadvantage
- Synergy: good (+4.2 avg)
- Lane outcome: NW delta −320g at 10 min — underperformed despite favorable matchup
```

### Logic

**Lineup line:** ally names with synergy scores, enemy names with WR — same content as existing `_lane_line()`.

**Avg matchup WR label:**

- < 47% → "slight disadvantage"
- 47–53% → "even matchup"
- \> 53% → "favorable matchup"
- Omitted if no enemy WR data available.

**Synergy label:**

- avg > +3 → "good"
- avg < −3 → "weak"
- otherwise → omitted
- Omitted if no synergy data available.

**Lane outcome line** (requires `net_worth_at_10 > 0` and `opponent_net_worth_at_10 > 0`):

NW delta = `net_worth_at_10 − opponent_net_worth_at_10`

| Matchup WR | NW delta | Suffix |
| --- | --- | --- |
| < 47% | negative | "expected given unfavorable matchup" |
| < 47% | positive | "outperformed a tough matchup" |
| > 53% | negative | "underperformed despite favorable matchup" |
| > 53% | positive | "expected given favorable matchup" |
| 47–53% | any | (no framing suffix) |
| No WR data | any | NW delta shown without framing |

**Returns `None`** if `lane_enemies` is empty (preserves existing skip behavior).

### Removal

`_lane_line()` is deleted after both call sites are migrated. No other callers exist.

---

## Frontend: Lane Lineup Chips

### Placement

Injected in `renderReport()` in `static/index.html`, between the metric cards (`#metricCards`) and the stats panel (`#statRows`).

### HTML structure

```html
<div class="lane-matchup" id="laneMatchup">
  <div class="lane-side">
    <span class="hero-chip chip-self">
      <img class="chip-icon" src="https://cdn...antimage.png">Wraith King
    </span>
    <span class="hero-chip chip-synergy-good">
      <img class="chip-icon" src="https://cdn...crystal_maiden.png">Crystal Maiden +4.2
    </span>
  </div>
  <div class="lane-vs">vs</div>
  <div class="lane-side">
    <span class="hero-chip chip-wr-bad">
      <img class="chip-icon" src="https://cdn...bristleback.png">Bristleback 44%
    </span>
    <span class="hero-chip chip-wr-neutral">
      <img class="chip-icon" src="https://cdn...lion.png">Lion 51%
    </span>
  </div>
</div>
```

Icon URLs come from `metrics.lane_hero_icons`. If a hero has no icon entry, the `<img>` is omitted and the chip shows text only.

The section is hidden (`display:none`) when `metrics.lane_enemies` is empty.

### Chip classes

**Ally chips (synergy score):**

- `chip-self` — player's own hero, no score, neutral white
- `chip-synergy-good` — score > +3, green tint
- `chip-synergy-neutral` — score −3 to +3, default chip style
- `chip-synergy-bad` — score < −3, red tint
- No score available → `chip-synergy-neutral`, name only

**Enemy chips (matchup WR):**

- `chip-wr-good` — WR > 53%, green tint (favorable for us)
- `chip-wr-neutral` — WR 47–53%, default
- `chip-wr-bad` — WR < 47%, red tint (unfavorable for us)
- No WR available → `chip-wr-neutral`, name only

### CSS

Chips use pill shape (`border-radius: 999px`), small font, subtle background tint. The `.chip-icon` is a small inline image (`height: 18px`, `border-radius: 3px`, `margin-right: 4px`). Colors use CSS variables consistent with the existing palette (`--accent-green`, `--accent-red`, `--text-muted`). No external dependencies.

---

## Testing

- `tests/test_prompt.py` — unit tests for `_laning_phase_block()` covering: no enemies (returns None), enemies with WR + synergy, enemies without WR data, all four expected-vs-actual interpretations.
- Manual UI smoke test: load a cached match with lane data, verify chips and icons render; load a match without lane data, verify section is hidden.
