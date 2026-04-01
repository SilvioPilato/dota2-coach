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
- In the `/recent-matches/{account_id}` handler, pass `game_mode` and `lobby_type` from the raw OpenDota response into each `MatchSummary`
- OpenDota's `/players/{account_id}/recentMatches` already returns both fields; no API change needed

### Frontend

Add a `gameTypeLabel(lobbyType, gameMode)` function in `index.html` that maps integer IDs to a human-readable string:

- **Lobby type** (primary label): Ranked (7), Unranked (0), Turbo (by game_mode), etc.
- **Game mode** (secondary label, only shown when it adds information beyond the lobby type):
  - Default All Pick (1) → omit
  - Turbo (23) → show "Turbo" as the primary label (supersedes lobby type)
  - Captains Mode (2) → append "· CM"
  - Random Draft (5) → append "· Random"
  - All Random (8) → append "· All Random"
  - Other modes → omit secondary label

**Key collapsing rules:**
- Ranked + All Pick → `"Ranked"`
- Unranked + All Pick → `"Unranked"`
- Any + Turbo → `"Turbo"`
- Ranked + CM → `"Ranked · CM"`
- Unranked + Random Draft → `"Unranked · Random"`

**UI placement:** Append the label to the existing `.sidebar-meta` line:
```
Apr 1 · 34:12 · Ranked
```

---

## Feature 2: Infinite Scroll with Pagination

### Backend

- Add optional `offset: int = 0` query parameter to `GET /recent-matches/{account_id}`
- Extend `opendota.get_recent_matches(account_id, limit, offset)` to pass `offset` to the OpenDota API
- The endpoint already supports `limit`; OpenDota's `/recentMatches` supports both `limit` and `offset`

### Frontend

**Page size:** 20 matches per page (unchanged from current single fetch)

**Loading strategy:**
- `loadSidebar(accountId)` resets state and calls `loadSidebarPage(accountId, offset=0)`
- `loadSidebarPage(accountId, offset)` fetches one page and **appends** rows to the list (does not clear)
- A sentinel `<div id="sidebarSentinel">` sits at the bottom of `#sidebarList`, below all rows
- An `IntersectionObserver` watches the sentinel; when it enters the viewport, it calls `loadSidebarPage` with the next offset
- **Termination:** if a page returns fewer than 20 rows, the observer is disconnected and the sentinel is removed
- **Loading state:** while a page fetch is in flight, a small spinner replaces the sentinel text; a lock prevents concurrent fetches

**State variables (module-level):**
```js
let sidebarOffset = 0;
let sidebarLoading = false;
let sidebarDone = false;
let sidebarObserver = null;
let sidebarAccountId = null;
```

**Error handling:** On fetch error, show an inline retry message in the sentinel area. Do not clear already-loaded rows.

---

## Data Flow

```
User scrolls → IntersectionObserver fires
  → loadSidebarPage(accountId, offset)
    → GET /recent-matches/{account_id}?offset={offset}
      → opendota.get_recent_matches(account_id, limit=20, offset=offset)
        → OpenDota /players/{id}/recentMatches?limit=20&offset={offset}
    → append rows to #sidebarList
    → if rows < 20: disconnect observer, remove sentinel
    → else: increment offset, re-observe sentinel
```

---

## Files Changed

| File | Changes |
|------|---------|
| `dota_coach/api.py` | `MatchSummary` gains `game_mode`, `lobby_type`; endpoint gains `offset` param |
| `dota_coach/opendota.py` | `get_recent_matches` gains `offset` param |
| `static/index.html` | `gameTypeLabel()`, updated `renderSidebar()` to append + sentinel, `IntersectionObserver` setup |

---

## Out of Scope

- Filtering by game type in the sidebar
- Caching paginated results in localStorage
- Server-side pagination cursor (OpenDota's offset is sufficient)
