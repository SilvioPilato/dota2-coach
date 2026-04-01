# Recent Game History Enhancements — Design Spec

**Date:** 2026-04-01
**Issues:** dota-analysis-ffg, dota-analysis-8xy
**Status:** Approved

---

## Overview

Two related enhancements to the sidebar's recent game history:

1. **Game type display** — surface lobby type and game mode (e.g. "Ranked", "Ranked · Turbo") in each sidebar row
2. **Infinite scroll with pagination** — replace the single 20-match fetch with paginated loading triggered by scrolling

---

## Feature 1: Game Type Display

### Backend

- Add `game_mode: int` and `lobby_type: int` fields to the `MatchSummary` Pydantic model in `api.py`
- In the `/recent-matches/{account_id}` handler's `for m in raw:` loop, add explicit `.get()` calls:

  ```python
  game_mode=m.get("game_mode", 0),
  lobby_type=m.get("lobby_type", 0),
  ```

- OpenDota's source endpoint already returns both fields; no external API change needed

### Frontend

Add a `gameTypeLabel(lobbyType, gameMode)` function in `index.html` that maps integer IDs to a human-readable string.

**Turbo is identified by `game_mode === 23`** (it is a game mode, not a lobby type). `lobby_type` is irrelevant when `game_mode === 23` — all Turbo variants (including Ranked Turbo) collapse to the single label `"Turbo"`. This is an intentional simplification: Turbo ranking is not meaningful for coaching purposes.

**Lobby type labels** (when `game_mode !== 23`):

| `lobby_type` | Label |
|---|---|
| 7 | Ranked |
| 0 | Unranked |
| other | "" |

**Game mode secondary label** (appended when it adds information beyond lobby type):

| `game_mode` | Secondary |
|---|---|
| 1 (All Pick) | omit |
| 2 (CM) | "CM" |
| 5 (Random Draft) | "Random" |
| 8 (All Random) | "All Random" |
| 23 (Turbo) | supersedes lobby label → "Turbo" |
| other | omit |

**Collapsing rules:**

- Any + Turbo (`game_mode === 23`) → `"Turbo"` (regardless of lobby_type)
- Ranked + All Pick → `"Ranked"`
- Unranked + All Pick → `"Unranked"`
- Ranked + CM → `"Ranked · CM"`
- Unranked + Random Draft → `"Unranked · Random"`
- Empty lobby + no notable mode → `""` (omit the label entirely from sidebar-meta)

**UI placement:** Append the label (when non-empty) to the existing `.sidebar-meta` line:

```
Apr 1 · 34:12 · Ranked
```

---

## Feature 2: Infinite Scroll with Pagination

### OpenDota API choice

`/players/{account_id}/recentMatches` does **not** support pagination — it always returns the last 20 matches. Pagination requires `/players/{account_id}/matches`, which supports `limit` and `offset` query parameters and returns the full match history. The response shape is identical for the fields we use.

### Backend

**`opendota.py`**: Add a new function `get_paginated_matches(account_id, limit=20, offset=0)` that calls `/players/{account_id}/matches` with `raise_for_status()`. Note: private profiles return `200 []` from OpenDota (not 403), so an empty array is handled naturally by the empty-state logic.

**`api.py`**: Update `recent_matches` handler:

- Add `offset: int = 0` query parameter to the function signature
- Replace the import `from dota_coach.opendota import get_recent_matches as opendota_recent` with `from dota_coach.opendota import get_paginated_matches`
- Replace `await opendota_recent(account_id, limit=20)` with `await get_paginated_matches(account_id, limit=20, offset=offset)`

The existing `get_recent_matches` function in `opendota.py` is left untouched (other callers may exist). The existing `from dota_coach.history import get_match_history` import is unrelated — the new opendota function is intentionally named `get_paginated_matches` to avoid collision.

### Frontend

The existing `renderSidebar(matches)` function is **removed entirely** and replaced by the append-rows logic inside `loadSidebarPage` (described below). The existing `loadSidebar(accountId)` function is **replaced** with the new implementation. `loadSavedAccount()` continues to call `loadSidebar(saved)` unchanged — the signature is preserved.

**Page size:** 20 matches per page

**Module-level state** (declared at the top of the script block, alongside existing globals):

```js
let sidebarOffset = 0;
let sidebarLoading = false;
let sidebarDone = false;
let sidebarObserver = null;
let sidebarAccountId = null;
```

**`loadSidebar(accountId)` — resets and starts fresh:**

1. Disconnect and null `sidebarObserver` if it exists (prevents stale observer firing on account change)
2. Reset `sidebarOffset = 0`, `sidebarLoading = false`, `sidebarDone = false`, `sidebarAccountId = accountId`
3. Clear all rows from `#sidebarList` except `#sidebarEmpty` and `#sidebarError` (same pattern as existing code)
4. Hide `#sidebarEmpty` and `#sidebarError`
5. Insert `#sidebarSentinel` div as the **last child** of `#sidebarList` (after the placeholder divs, which are hidden)
6. Set up `IntersectionObserver`:
   ```js
   sidebarObserver = new IntersectionObserver(entries => {
     if (entries[0].isIntersecting) loadSidebarPage(sidebarAccountId);
   }, { threshold: 0.1 });
   sidebarObserver.observe(document.getElementById('sidebarSentinel'));
   ```
7. Call `loadSidebarPage(accountId)` immediately (first page)

**`loadSidebarPage(accountId)` — fetches one page:**

1. Guard: `if (sidebarLoading || sidebarDone || accountId !== sidebarAccountId) return;`
2. Set `sidebarLoading = true`; show spinner in sentinel
3. `fetch(/recent-matches/${accountId}?offset=${sidebarOffset})`
4. On success:
   - If 0 rows and `sidebarOffset === 0`: show `#sidebarEmpty`, set `sidebarDone = true`, disconnect observer, remove sentinel
   - Else if rows < 20: append rows, set `sidebarDone = true`, disconnect observer, remove sentinel
     *(do NOT increment sidebarOffset — termination makes it irrelevant)*
   - Else (rows === 20): append rows, increment `sidebarOffset += 20`
5. On error: show inline retry button in sentinel (clicking it calls `loadSidebarPage(accountId)`)
6. `finally`: set `sidebarLoading = false`; restore sentinel to idle state (unless `sidebarDone` or showing error)

**Appending rows:** Extract the row-building logic from the current `renderSidebar` into a helper `buildMatchRow(m)` that returns a DOM element. `loadSidebarPage` calls `buildMatchRow` for each match and inserts rows before the sentinel.

---

## Data Flow

```
loadSidebar(accountId)
  → disconnect old observer, reset state
  → insert sentinel, set up IntersectionObserver
  → loadSidebarPage(accountId)  [immediately]

User scrolls → IntersectionObserver fires
  → loadSidebarPage(accountId)
    → guard: skip if loading, done, or stale account
    → GET /recent-matches/{account_id}?offset={sidebarOffset}
      → get_paginated_matches(account_id, limit=20, offset=offset)
        → OpenDota /players/{id}/matches?limit=20&offset={offset}
    → insert rows before sentinel
    → if rows < 20: sidebarDone=true, disconnect observer, remove sentinel
    → else: sidebarOffset += 20
```

---

## Files Changed

| File | Changes |
|---|---|
| `dota_coach/api.py` | `MatchSummary` gains `game_mode`, `lobby_type`; endpoint gains `offset` param; switches to `get_paginated_matches` |
| `dota_coach/opendota.py` | Add `get_paginated_matches(account_id, limit, offset)` using `/players/{id}/matches` |
| `static/index.html` | `gameTypeLabel()`, `buildMatchRow()`, new `loadSidebar()`, new `loadSidebarPage()`, remove `renderSidebar()`, add module-level state vars |

---

## Out of Scope

- Filtering by game type in the sidebar
- Caching paginated results in localStorage
- Server-side pagination cursor
