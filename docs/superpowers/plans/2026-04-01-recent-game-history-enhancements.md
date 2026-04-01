# Recent Game History Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add game type labels (e.g. "Ranked · CM") to sidebar match rows and replace the single-fetch sidebar with paginated infinite scroll.

**Architecture:** Backend gets a new `get_paginated_matches` function in `opendota.py` hitting `/players/{id}/matches` with `limit`/`offset`, `MatchSummary` gains `game_mode`/`lobby_type` fields, and the endpoint gains an `offset` query param. Frontend replaces `renderSidebar` with `buildMatchRow` + `loadSidebarPage` + an `IntersectionObserver` sentinel pattern.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, httpx (async), pytest, vanilla JS (ES2020), IntersectionObserver API

---

## File Map

| File | Action | What changes |
|---|---|---|
| `dota_coach/opendota.py` | Modify | Add `get_paginated_matches(account_id, limit, offset)` |
| `dota_coach/api.py` | Modify | `MatchSummary` + `game_mode`/`lobby_type`; endpoint `offset` param; switch to `get_paginated_matches` |
| `tests/test_opendota.py` | Create | Tests for `get_paginated_matches` |
| `tests/test_recent_matches_api.py` | Create | Tests for updated `/recent-matches` endpoint |
| `static/index.html` | Modify | `gameTypeLabel()`, `buildMatchRow()`, `loadSidebar()`, `loadSidebarPage()`, state vars; remove `renderSidebar()` |

---

## Task 1: `get_paginated_matches` in `opendota.py`

**Files:**
- Modify: `dota_coach/opendota.py`
- Create: `tests/test_opendota.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_opendota.py`:

```python
"""Tests for dota_coach.opendota — get_paginated_matches."""
from __future__ import annotations

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from dota_coach.opendota import get_paginated_matches


@pytest.mark.asyncio
async def test_get_paginated_matches_default_params():
    """Calls /players/{id}/matches with limit=20 and offset=0 by default."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [{"match_id": 1}, {"match_id": 2}]

    with patch("dota_coach.opendota.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_paginated_matches(account_id=12345678)

    mock_client.get.assert_called_once_with(
        "https://api.opendota.com/api/players/12345678/matches",
        params={"limit": 20, "offset": 0},
    )
    assert result == [{"match_id": 1}, {"match_id": 2}]


@pytest.mark.asyncio
async def test_get_paginated_matches_custom_offset():
    """Passes custom limit and offset to the API."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = []

    with patch("dota_coach.opendota.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_paginated_matches(account_id=12345678, limit=20, offset=40)

    mock_client.get.assert_called_once_with(
        "https://api.opendota.com/api/players/12345678/matches",
        params={"limit": 20, "offset": 40},
    )
    assert result == []


@pytest.mark.asyncio
async def test_get_paginated_matches_raises_on_http_error():
    """Propagates HTTP errors via raise_for_status."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "502", request=MagicMock(), response=MagicMock()
    )

    with patch("dota_coach.opendota.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(httpx.HTTPStatusError):
            await get_paginated_matches(account_id=12345678)
```

- [ ] **Step 2: Install pytest-asyncio if needed, then run tests to confirm they fail**

```bash
cd C:/Users/Silvio/Dev/dota-analysis
pip install pytest-asyncio --quiet
pytest tests/test_opendota.py -v
```

Expected: `ImportError: cannot import name 'get_paginated_matches'`

- [ ] **Step 3: Add `get_paginated_matches` to `dota_coach/opendota.py`**

Add after the existing `get_recent_matches` function (after line 36):

```python
async def get_paginated_matches(account_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    """GET /players/{account_id}/matches — paginated match history with limit/offset."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{BASE_URL}/players/{account_id}/matches",
            params={"limit": limit, "offset": offset},
        )
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_opendota.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add dota_coach/opendota.py tests/test_opendota.py
git commit -m "feat: add get_paginated_matches to opendota client"
```

---

## Task 2: Update `MatchSummary` + `/recent-matches` endpoint

**Files:**
- Modify: `dota_coach/api.py`
- Create: `tests/test_recent_matches_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recent_matches_api.py`:

```python
"""Tests for GET /recent-matches/{account_id} endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dota_coach.api import app, MatchSummary

client = TestClient(app)


# ---------------------------------------------------------------------------
# MatchSummary model tests
# ---------------------------------------------------------------------------

def test_match_summary_has_game_mode_and_lobby_type():
    """MatchSummary must include game_mode and lobby_type fields."""
    s = MatchSummary(
        match_id=1,
        hero_id=1,
        hero_name="Axe",
        start_time=1700000000,
        duration_seconds=1800,
        won=True,
        kills=5,
        deaths=2,
        assists=3,
        analyzed=False,
        replay_available=True,
        game_mode=1,
        lobby_type=7,
    )
    assert s.game_mode == 1
    assert s.lobby_type == 7


def test_match_summary_game_mode_defaults_to_zero():
    """game_mode and lobby_type default to 0 if not provided."""
    s = MatchSummary(
        match_id=1, hero_id=1, hero_name="", start_time=0,
        duration_seconds=0, won=False, kills=0, deaths=0, assists=0,
        analyzed=False, replay_available=False,
    )
    assert s.game_mode == 0
    assert s.lobby_type == 0


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

FAKE_MATCH = {
    "match_id": 9000000001,
    "start_time": 1700000000,
    "player_slot": 0,
    "radiant_win": True,
    "hero_id": 2,
    "duration": 1800,
    "kills": 10,
    "deaths": 1,
    "assists": 5,
    "game_mode": 23,
    "lobby_type": 0,
}


def test_recent_matches_returns_game_mode_and_lobby_type():
    """Endpoint serializes game_mode and lobby_type into each row."""
    with patch("dota_coach.history.get_analyzed_ids", return_value=set()), \
         patch("dota_coach.opendota.get_paginated_matches", new=AsyncMock(return_value=[FAKE_MATCH])):
        resp = client.get("/recent-matches/12345678")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_mode"] == 23
    assert data[0]["lobby_type"] == 0


def test_recent_matches_offset_param_passed_through():
    """?offset=20 is forwarded to get_paginated_matches."""
    mock_fn = AsyncMock(return_value=[])
    with patch("dota_coach.history.get_analyzed_ids", return_value=set()), \
         patch("dota_coach.opendota.get_paginated_matches", new=mock_fn):
        client.get("/recent-matches/12345678?offset=20")

    mock_fn.assert_called_once_with(12345678, limit=20, offset=20)


def test_recent_matches_offset_defaults_to_zero():
    """When ?offset is absent, defaults to 0."""
    mock_fn = AsyncMock(return_value=[])
    with patch("dota_coach.history.get_analyzed_ids", return_value=set()), \
         patch("dota_coach.opendota.get_paginated_matches", new=mock_fn):
        client.get("/recent-matches/12345678")

    mock_fn.assert_called_once_with(12345678, limit=20, offset=0)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_recent_matches_api.py -v
```

Expected: `test_match_summary_has_game_mode_and_lobby_type` FAIL (field missing), others also fail

- [ ] **Step 3: Update `MatchSummary` in `dota_coach/api.py`**

Add two fields after `replay_available` (around line 53):

```python
    game_mode: int = 0     # OpenDota game_mode ID (23 = Turbo, 1 = All Pick, etc.)
    lobby_type: int = 0    # OpenDota lobby_type ID (7 = Ranked, 0 = Unranked, etc.)
```

- [ ] **Step 4: Update the `/recent-matches` handler in `dota_coach/api.py`**

**4a.** Replace the import inside the handler (around line 376):

```python
# OLD:
from dota_coach.opendota import get_recent_matches as opendota_recent

# NEW:
from dota_coach.opendota import get_paginated_matches
```

**4b.** Add `offset: int = 0` to the function signature (line 372):

```python
# OLD:
async def recent_matches(account_id: int):

# NEW:
async def recent_matches(account_id: int, offset: int = 0):
```

**4c.** Replace the call (around line 384):

```python
# OLD:
raw = await opendota_recent(account_id, limit=20)

# NEW:
raw = await get_paginated_matches(account_id, limit=20, offset=offset)
```

**4d.** Add `.get()` calls inside the `for m in raw:` loop in `MatchSummary(...)` (after `replay_available=`):

```python
            game_mode=m.get("game_mode", 0),
            lobby_type=m.get("lobby_type", 0),
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_recent_matches_api.py tests/test_opendota.py -v
```

Expected: all PASSED

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
pytest -v
```

Expected: all existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add dota_coach/api.py tests/test_recent_matches_api.py
git commit -m "feat: add game_mode/lobby_type to MatchSummary, add offset param to /recent-matches"
```

---

## Task 3: `gameTypeLabel()` + game type in sidebar-meta

**Files:**
- Modify: `static/index.html`

This task adds the `gameTypeLabel` function and wires it into the row HTML. It does not touch pagination yet — `renderSidebar` is still intact at the end of this task.

- [ ] **Step 1: Add module-level pagination state vars to `index.html`**

Find the block of `let` declarations near the top of the `<script>` block (around line 144 where `let matchReport = null` is). Add after those declarations:

```js
// Sidebar pagination state
let sidebarOffset = 0;
let sidebarLoading = false;
let sidebarDone = false;
let sidebarObserver = null;
let sidebarAccountId = null;
```

- [ ] **Step 2: Add `gameTypeLabel` function**

Add this function just before the `renderSidebar` function (before line 751):

```js
/* ------------------------------------------------------------------ */
/*  Game type label                                                    */
/* ------------------------------------------------------------------ */
function gameTypeLabel(lobbyType, gameMode) {
  // Turbo (game_mode 23) supersedes lobby type entirely
  if (gameMode === 23) return 'Turbo';

  const LOBBY = { 7: 'Ranked', 0: 'Unranked' };
  const MODE  = { 2: 'CM', 5: 'Random', 8: 'All Random' };

  const lobbyLabel = LOBBY[lobbyType] ?? '';
  const modeLabel  = MODE[gameMode]   ?? '';

  if (lobbyLabel && modeLabel) return `${lobbyLabel} · ${modeLabel}`;
  if (lobbyLabel) return lobbyLabel;
  if (modeLabel)  return modeLabel;
  return '';
}
```

- [ ] **Step 3: Wire `gameTypeLabel` into `.sidebar-meta` inside `renderSidebar`**

In `renderSidebar`, find the `.sidebar-meta` line inside the `row.innerHTML` template (around line 799). Change it from:

```js
        <div class="sidebar-meta">${date} · ${duration}</div>
```

to:

```js
        <div class="sidebar-meta">${date} · ${duration}${(() => { const gt = gameTypeLabel(m.lobby_type ?? 0, m.game_mode ?? 0); return gt ? ' · ' + gt : ''; })()}</div>
```

- [ ] **Step 4: Manual smoke test**

Start the server: `uvicorn dota_coach.api:app --reload`

Open the app, enter a Player ID, and verify:
- Ranked All Pick matches show just `"Ranked"`
- Turbo matches show just `"Turbo"`
- CM/Random Draft matches show the combined label

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat: add game type label to sidebar match rows"
```

---

## Task 4: Infinite scroll — replace `renderSidebar` with paginated loading

**Files:**
- Modify: `static/index.html`

This task replaces the single-fetch + `renderSidebar` pattern with `buildMatchRow` + `loadSidebarPage` + `IntersectionObserver`.

- [ ] **Step 1: Extract `buildMatchRow(m)` from `renderSidebar`**

Add a new `buildMatchRow(m)` function just after `gameTypeLabel` and before `renderSidebar`. It contains everything inside the `matches.forEach(m => { ... })` block of `renderSidebar`, returning the constructed `row` element instead of appending it:

```js
function buildMatchRow(m) {
  const expired  = !m.replay_available;
  const analyzed = m.analyzed;

  const stateClass = expired ? 'state-expired' : (analyzed ? 'state-analyzed' : 'state-ready');

  const initials = m.hero_name
    ? m.hero_name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
    : '??';

  const date     = new Date(m.start_time * 1000).toLocaleDateString();
  const mins     = Math.floor(m.duration_seconds / 60);
  const secs     = m.duration_seconds % 60;
  const duration = `${mins}:${secs.toString().padStart(2, '0')}`;
  const gt       = gameTypeLabel(m.lobby_type ?? 0, m.game_mode ?? 0);
  const metaLine = `${date} · ${duration}${gt ? ' · ' + gt : ''}`;

  const row = document.createElement('div');
  row.className = `sidebar-row ${stateClass}${expired ? ' sidebar-expired' : ''}`;
  row.dataset.matchId       = m.match_id;
  row.dataset.heroName      = m.hero_name;
  row.dataset.analyzed      = m.analyzed;
  row.dataset.replayAvailable = m.replay_available;

  row.innerHTML = `
    <div class="sidebar-avatar">${initials}</div>
    <div class="sidebar-info">
      <div class="sidebar-hero">${m.hero_name || 'Unknown'}</div>
      <div class="sidebar-meta">${metaLine}</div>
    </div>
    <div class="sidebar-badge-wrap">
      <span class="sidebar-badge ${m.won ? 'badge-win' : 'badge-loss'}">${m.won ? 'W' : 'L'}</span>
      ${!expired ? `<button class="sidebar-action" style="display:none">${analyzed ? 'Re-analyze' : 'Analyze'}</button>` : ''}
    </div>
    <span class="sidebar-dot"></span>
  `;

  // Hover behavior
  if (!expired) {
    const badge = row.querySelector('.sidebar-badge');
    const btn   = row.querySelector('.sidebar-action');
    row.addEventListener('mouseenter', () => { badge.style.display = 'none'; btn.style.display = ''; });
    row.addEventListener('mouseleave', () => { badge.style.display = '';    btn.style.display = 'none'; });
  }

  // Click to view cached report
  if (analyzed && !expired) {
    row.addEventListener('click', async function(e) {
      if (e.target.classList.contains('sidebar-action')) return;
      const pid      = localStorage.getItem('dota_coach_account_id') || lastPlayerId || '';
      const cacheKey = `dota_analysis_${m.match_id}_${pid}`;
      const cached   = localStorage.getItem(cacheKey);
      if (cached) { try { renderReport(JSON.parse(cached)); return; } catch {} }
      try {
        const res = await fetch(`/report/${pid}/${m.match_id}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const report = await res.json();
        localStorage.setItem(cacheKey, JSON.stringify(report));
        renderReport(report);
      } catch (err) { console.error('Failed to load report from server:', err); }
    });
  }

  // Analyze button
  if (!expired) {
    const btn = row.querySelector('.sidebar-action');
    if (btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        document.getElementById('matchId').value = m.match_id;
        if (analyzed) { runAnalysis(null, { force_reanalyze: true }); }
        else           { runAnalysis(); }
      });
    }
  }

  return row;
}
```

- [ ] **Step 2: Replace `loadSidebar` with paginated version**

Find the existing `loadSidebar` function (around line 680) and replace the entire function body with:

```js
async function loadSidebar(accountId) {
  // Tear down any previous observer to prevent stale account triggering loads
  if (sidebarObserver) { sidebarObserver.disconnect(); sidebarObserver = null; }

  // Reset pagination state
  sidebarOffset    = 0;
  sidebarLoading   = false;
  sidebarDone      = false;
  sidebarAccountId = accountId;

  const list    = document.getElementById('sidebarList');
  const emptyEl = document.getElementById('sidebarEmpty');
  const errorEl = document.getElementById('sidebarError');

  // Clear existing rows, keep placeholder divs
  Array.from(list.children).forEach(c => {
    if (!c.id || (c.id !== 'sidebarEmpty' && c.id !== 'sidebarError')) c.remove();
  });
  emptyEl.style.display = 'none';
  errorEl.style.display = 'none';

  // Insert sentinel at the bottom
  const sentinel = document.createElement('div');
  sentinel.id = 'sidebarSentinel';
  sentinel.style.cssText = 'height:1px;display:flex;align-items:center;justify-content:center;padding:8px 0;';
  list.appendChild(sentinel);

  // Observe sentinel to trigger next-page loads
  sidebarObserver = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) loadSidebarPage(sidebarAccountId);
  }, { threshold: 0.1 });
  sidebarObserver.observe(sentinel);

  // Load first page immediately
  await loadSidebarPage(accountId);
}
```

- [ ] **Step 3: Add `loadSidebarPage` function**

Add immediately after the new `loadSidebar` function:

```js
async function loadSidebarPage(accountId) {
  if (sidebarLoading || sidebarDone || accountId !== sidebarAccountId) return;

  sidebarLoading = true;
  const list     = document.getElementById('sidebarList');
  const emptyEl  = document.getElementById('sidebarEmpty');
  const sentinel = document.getElementById('sidebarSentinel');

  if (sentinel) sentinel.innerHTML = '<span style="font-size:12px;color:var(--text-muted)">Loading…</span>';

  try {
    const res = await fetch(`/recent-matches/${accountId}?offset=${sidebarOffset}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const matches = await res.json();

    if (matches.length === 0 && sidebarOffset === 0) {
      // No matches at all
      emptyEl.style.display = '';
      sidebarDone = true;
      if (sidebarObserver) { sidebarObserver.disconnect(); sidebarObserver = null; }
      if (sentinel) sentinel.remove();
      return;
    }

    // Append rows before sentinel
    matches.forEach(m => {
      const row = buildMatchRow(m);
      if (sentinel) list.insertBefore(row, sentinel);
      else          list.appendChild(row);
    });

    if (matches.length < 20) {
      // Last page — no more data
      sidebarDone = true;
      if (sidebarObserver) { sidebarObserver.disconnect(); sidebarObserver = null; }
      if (sentinel) sentinel.remove();
    } else {
      sidebarOffset += 20;
      if (sentinel) sentinel.innerHTML = '';
    }
  } catch (e) {
    if (sentinel) {
      sentinel.innerHTML = `
        <span style="font-size:12px;color:var(--text-muted)">Failed to load.
          <button onclick="loadSidebarPage('${accountId}')"
                  style="margin-left:4px;background:none;border:none;color:var(--accent);cursor:pointer;font-size:12px;">
            Retry
          </button>
        </span>`;
    }
  } finally {
    sidebarLoading = false;
  }
}
```

- [ ] **Step 4: Remove `renderSidebar` and `showSidebarError`**

Delete the entire `renderSidebar(matches)` function (lines ~751–862) and the `showSidebarError()` function that follows it. The new code no longer calls either.

> Note: search for any remaining call sites of `showSidebarError` in the file and remove them too.

- [ ] **Step 5: Manual smoke test**

Start the server: `uvicorn dota_coach.api:app --reload`

Verify:
1. First 20 matches load on sidebar open
2. Scrolling to the bottom of the sidebar loads the next 20
3. Changing Player ID resets the list (old rows disappear, new ones load)
4. Game type label appears correctly on rows
5. Turbo matches show "Turbo", Ranked shows "Ranked", CM shows "Ranked · CM"
6. Analyze / Re-analyze buttons still work
7. Click on analyzed row still loads the report

- [ ] **Step 6: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat: replace renderSidebar with infinite scroll pagination"
```

---

## Task 5: Close issues and push

- [ ] **Step 1: Close beads issues**

```bash
bd close dota-analysis-ffg dota-analysis-8xy
```

- [ ] **Step 2: Push to remote**

```bash
git push
```
