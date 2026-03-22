# Replay & Analysis Caching — Design Spec

**Date:** 2026-03-22
**Status:** Approved

---

## Problem

Every call to `/analyze` re-downloads the replay (~100–500 MB), re-parses it (CPU-intensive), and re-calls the LLM (slow + costs money), even when the exact same match and player were analyzed seconds ago. There is also no browser-side persistence, so a page refresh loses the result.

---

## Goals

1. Cache the full analysis result on disk so repeat requests for the same match+player+role return cheaply (still pays OpenDota API call + role detection, but skips download + parse + LLM).
2. Cache the decompressed `.dem` file on disk so re-analysis (e.g. after a role correction) skips the download.
3. Persist the last result in the browser so a page refresh does not require a re-submit.
4. Allow the user to explicitly bypass each cache layer independently.

---

## Cache Layout

All files live in the existing `~/.dota_coach/cache/` directory (created by `enricher.py`).

| File | TTL | Key |
| --- | --- | --- |
| `replay_{match_id}.dem` | 7 days | `match_id` (integer) |
| `analysis_{match_id}_{account_id}_{role}.json` | 7 days | `match_id` + `account_id` + `role` (all integers) |

The `role` in the filename is the **resolved integer role (1–5)**, not the label string, for stability.

TTL is enforced by `mtime` check, consistent with the existing pattern in `enricher.py`.

The 7-day TTL aligns with Valve's replay CDN expiry window. A `.dem` file stored on day 3 may still be served from cache on day 6 even after Valve has expired it from the CDN — this is intentional and acceptable for a personal tool.

**Note on disk space:** Decompressed `.dem` files are typically 50–200 MB each. A comment in `cache.py` should remind users that the cache directory can be manually cleared.

---

## Components

### `dota_coach/cache.py` (new)

Owns all cache file operations for replay and analysis artifacts. Five functions:

```python
ANALYSIS_TTL = 3600 * 24 * 7   # 7 days
DEM_TTL      = 3600 * 24 * 7   # 7 days

def read_analysis_cache(match_id: int, account_id: int, role: int) -> dict | None
def write_analysis_cache(match_id: int, account_id: int, role: int, data: dict) -> None
def get_dem_cache_path(match_id: int) -> Path
def is_dem_cache_fresh(match_id: int) -> bool    # checks existence + TTL
def invalidate_dem_cache(match_id: int) -> None  # no-op if file absent
```

- `read_analysis_cache`: reads and parses JSON; returns `None` on any error (missing, expired, or `json.JSONDecodeError`). Log a `WARNING` on `JSONDecodeError` so users can diagnose corrupt caches.
- `write_analysis_cache`: serialises `data` via `json.dumps`; logs a `WARNING` and returns without writing if serialisation fails (e.g. non-JSON-serialisable field). Writes atomically — serialise to a `.tmp` file then `rename()` over the target, so a crash mid-write never leaves a corrupt cache file.
- `invalidate_dem_cache`: deletes `cache/replay_{match_id}.dem` if present. Owns the deletion logic; the downloader calls this rather than performing its own file deletion.
- Both `write_analysis_cache` and `get_dem_cache_path` call `mkdir(parents=True, exist_ok=True)` on `CACHE_DIR` before use, consistent with `enricher.py`'s `_ensure_cache_dir()`.

---

### `dota_coach/downloader.py` changes

Signature becomes:

```python
@asynccontextmanager
async def download_and_decompress(
    replay_url: str | None,
    match_id: int,
    force_redownload: bool = False,
):
```

Logic:

1. If `force_redownload=True`: call `cache.invalidate_dem_cache(match_id)` to remove any existing cached file.
2. If `cache.is_dem_cache_fresh(match_id)`: yield `cache.get_dem_cache_path(match_id)` directly. **The HEAD check is skipped** — the file is already decompressed locally.
3. Otherwise (no cached file, or just invalidated):
   - If `replay_url` is `None`: raise `ReplayExpiredError` immediately (existing guard retained).
   - Perform the existing HEAD check for replay expiry.
   - Download the `.bz2` into a `tempfile.TemporaryDirectory()` (cleaned up automatically on context exit).
   - Decompress `.bz2` → write `.dem` atomically: decompress to a `.tmp` path inside `CACHE_DIR`, then `rename()` to the final `cache/replay_{match_id}.dem`.
   - Yield the final cache path.
4. **Context manager exit**: never deletes the `.dem` cache file. The temporary `.bz2` directory from step 3 is cleaned up by its own `TemporaryDirectory` context. The `.dem` in `CACHE_DIR` is persistent.

Note: this is a **breaking change** to the function signature. The call site in `api.py` (line 121) must be updated to pass `match_id_int` and `force_redownload`.

---

### `dota_coach/api.py` changes

`AnalyzeRequest` gains two flags:

```python
force_reanalyze: bool = False    # skip analysis cache; re-run full pipeline (keep .dem)
force_redownload: bool = False   # delete .dem and re-download; implies force_reanalyze
```

`force_redownload=True` always implies a full re-run (including LLM), because if the `.dem` changes the parsed metrics may differ, making any cached analysis result stale.

**Pipeline order** (updated to reflect cache read position):

Steps 1–4 run unconditionally on every request — they are cheap (network metadata fetch + role detection) and necessary to build the cache key:

1. Check parser sidecar health.
2. Fetch `match_meta` from OpenDota (`get_match`).
3. Resolve `account_id` from `player_id` or fallback.
4. Resolve `role` (override or auto-detect).

**After step 4**, attempt cache read if neither flag is set:

```python
if not force_reanalyze and not force_redownload:
    cached = read_analysis_cache(match_id_int, account_id, role)
    if cached:
        return JSONResponse(content=cached)
```

Steps 5–12 (download, parse, extract, enrich, detect errors, LLM) run only on a cache miss or forced re-run.

After step 12 (on success):

```python
write_analysis_cache(match_id_int, account_id, role, report.model_dump())
```

The call to `download_and_decompress` is updated:

```python
async with download_and_decompress(replay_url, match_id_int, force_redownload=force_redownload) as dem_path:
```

---

### `static/index.html` changes

**localStorage persistence:**

- Key: `dota_analysis_{match_id}_{player_id}` (without role — simpler lookups; the most recent role run for a given match+player overwrites the previous one, which is acceptable for a personal tool).
- Value: the full `MatchReport` JSON (includes the resolved `role` field).
- **On page load**: do not auto-render. localStorage is only used when the user submits — before hitting the backend, check if a cached result exists for the submitted `(match_id, player_id)` and render it immediately. The Re-analyze / Re-download buttons then allow refreshing.
- **On successful `/analyze` response**: write the result to localStorage (overwriting any prior entry for the same key).

**`runAnalysis(roleOverride, flags)` signature:**

The existing `runAnalysis(roleOverride)` function gains a second parameter `flags = {}`:

```js
async function runAnalysis(roleOverride = null, flags = {}) {
    const body = { match_id, player_id };
    if (roleOverride) body.role_override = Number(roleOverride);  // integer, consistent with existing rerunWithRole()
    if (flags.force_reanalyze)  body.force_reanalyze  = true;
    if (flags.force_redownload) body.force_redownload = true;
    // ... existing fetch logic
}
```

The existing `rerunWithRole(role)` continues to call `runAnalysis(role)` unchanged — the `Number()` conversion is now inside `runAnalysis`, so `rerunWithRole` does not need to change.

**Three buttons in the submit area:**

| Button | Placement | Visibility | Behavior |
| --- | --- | --- | --- |
| **Analyze** | Always visible | Always | Normal submit — `runAnalysis()` |
| **Re-analyze** | After result panel | Only when a result is rendered | `runAnalysis(currentRole, {force_reanalyze: true})` |
| **Re-download replay** | After result panel | Only when a result is rendered | `runAnalysis(currentRole, {force_redownload: true})` |

`currentRole` is the `role_override` value currently selected in the role dropdown (or `null` for auto-detect).

---

## Error Handling

- Corrupt `.dem` (parse fails after cache hit): error surfaces normally. User can click **Re-download replay** to recover. The `.dem` is not auto-deleted on parse failure — that would be surprising; the user must explicitly request a re-download.
- Malformed analysis cache JSON: `read_analysis_cache` returns `None` and logs a `WARNING`; pipeline runs fresh and overwrites with a good result.
- `force_redownload` + download fails: the old `.dem` was already deleted by `invalidate_dem_cache`. The download failure surfaces as an HTTP 503/404 error. No corrupt cache is left because of the atomic rename strategy (the `.tmp` file is not renamed on failure).
- `invalidate_dem_cache` called when file does not exist: no-op, no error.

---

## Out of Scope

- Cache eviction UI (beyond the two force-refresh buttons)
- Shared/multi-user cache
- Redis or any external cache store
- Auto-clearing localStorage on cache invalidation (browser and server caches are managed independently)
