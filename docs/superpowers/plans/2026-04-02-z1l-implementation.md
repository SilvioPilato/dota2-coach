# z1l Implementation Plan — LLM Lane Context

**Spec:** [2026-04-01-llm-lane-context-design.md](../specs/2026-04-01-llm-lane-context-design.md)
**Status:** In Progress

---

## Tasks

### Task 1: Add `lane_hero_icons` field to `MatchMetrics`
- [ ] **File:** `dota_coach/models.py`
- [ ] Add `lane_hero_icons: dict[str, str] = {}` field to `MatchMetrics` class
- [ ] Field maps hero name → CDN icon URL (e.g., `"Anti-Mage" → "https://cdn.cloudflare.steamstatic.com/..."`)

### Task 2: Populate `lane_hero_icons` in enricher
- [ ] **File:** `dota_coach/enricher.py`
- [ ] Locate `enrich_lane_matchup()` function
- [ ] For each hero in `lane_enemies + lane_allies + [metrics.hero]`:
  - [ ] Look up hero in `heroes_data`
  - [ ] Extract `icon` field (relative path like `/apps/dota2/images/dota_react/heroes/icons/antimage.png?`)
  - [ ] Convert to full CDN URL: `https://cdn.cloudflare.steamstatic.com{icon_path_without_trailing_query}`
  - [ ] Store in `metrics.lane_hero_icons[hero_name] = url`

### Task 3: Create `_laning_phase_block()` in prompt
- [ ] **File:** `dota_coach/prompt.py`
- [ ] New helper function `_laning_phase_block(metrics, enrichment) -> str | None`
- [ ] Returns `None` if `lane_enemies` is empty
- [ ] Output format:
  ```
  LANING PHASE:
  - Lineup: {allies_with_synergy} vs {enemies_with_wr}
  - Avg matchup WR: {wr}% — {wr_label}
  - Synergy: {synergy_label}
  - Lane outcome: NW delta {delta}g at 10 min — {outcome_frame}
  ```
- [ ] WR label logic: <47% → "slight disadvantage", 47-53% → "even matchup", >53% → "favorable matchup"
- [ ] Synergy label: >+3 → "good", <-3 → "weak", else omit
- [ ] Lane outcome framing: see spec table (4 cases based on WR + NW delta sign)
- [ ] Replace two `_lane_line()` call sites:
  - [ ] In `build_user_message()` — find where `_lane_line()` is called
  - [ ] In `_build_chat_system_prompt()` — find where `_lane_line()` is called
- [ ] Delete `_lane_line()` function after migration

### Task 4: Unit tests for `_laning_phase_block()`
- [ ] **File:** `tests/test_prompt.py`
- [ ] Test: no enemies → returns None
- [ ] Test: enemies with full WR + synergy data → full block output
- [ ] Test: enemies without WR data → output without WR label
- [ ] Test: all four expected-vs-actual framings (unfavorable/positive, favorable/negative, etc.)

### Task 5: Frontend — Lane lineup chips
- [ ] **File:** `static/index.html`
- [ ] Locate `renderReport()` function
- [ ] Inject lane matchup HTML between metric cards and stats panel
- [ ] HTML structure:
  ```html
  <div class="lane-matchup" id="laneMatchup">
    <div class="lane-side"> {ally chips} </div>
    <div class="lane-vs">vs</div>
    <div class="lane-side"> {enemy chips} </div>
  </div>
  ```
- [ ] Generate chips dynamically from `metrics.lane_allies`, `metrics.lane_enemies`, `metrics.lane_ally_synergy_scores`, `metrics.lane_matchup_winrates`
- [ ] Chip classes:
  - Allies: `chip-self`, `chip-synergy-good` (>+3), `chip-synergy-neutral`, `chip-synergy-bad` (<-3)
  - Enemies: `chip-wr-good` (>53%), `chip-wr-neutral`, `chip-wr-bad` (<47%)
- [ ] Hide section when `lane_enemies` is empty
- [ ] Icons from `metrics.lane_hero_icons` (omit `<img>` if not present)

### Task 6: Frontend CSS
- [ ] **File:** `static/style.css`
- [ ] Styles for `.lane-matchup`, `.lane-side`, `.lane-vs`, `.hero-chip`, `.chip-icon`
- [ ] Pill shape: `border-radius: 999px`
- [ ] Icon: `height: 18px`, `border-radius: 3px`, `margin-right: 4px`
- [ ] Color classes: `chip-self`, `chip-synergy-good` (green), `chip-synergy-neutral`, `chip-synergy-bad` (red)
- [ ] Colors use CSS variables: `--accent-green`, `--accent-red`, `--text-muted`, etc.

### Task 7: Final
- [ ] Run full test suite — all tests passing
- [ ] Manual smoke test: load match with lane data, verify chips + icons render
- [ ] Commit and push

---

## Workflow

Execute tasks 1-4 (backend), then 5-6 (frontend), then 7 (final).
