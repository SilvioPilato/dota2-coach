# Local Benchmark Accumulation (Regular Matches)

**Date:** 2026-04-02
**Issue:** dota-analysis-5lc
**Status:** Approved

## Overview

After 30+ non-Turbo matches per hero are stored in the SQLite history DB, compute local percentile distributions (p25/median/p75) as a supplement to OpenDota global benchmarks. Both sources are shown side-by-side in the UI and integrated into error detection and LLM context. A metrics-only import pipeline lets users bootstrap historical data without running full LLM analysis on past matches.

Imported matches count toward the 30-match threshold — the intent is for the user to seed their history with past games so local benchmarks activate immediately.

---

## Section 1: Data Models

Two new models added to `models.py`:

```python
class LocalBenchmark(BaseModel):
    metric: str              # "gold_per_min", "xp_per_min", "last_hits_per_min"
    player_value: float
    player_pct: float        # 0.0–1.0 percentile vs local sample
    p25: float
    median: float
    p75: float
    sample_size: int         # number of non-turbo matches in sample

class LocalBenchmarkProgress(BaseModel):
    hero: str
    matches_stored: int
    threshold: int = 30      # matches needed before local benchmarks activate
```

`EnrichmentContext` gains:
- `local_benchmarks: list[LocalBenchmark] = []`
- `local_benchmark_progress: LocalBenchmarkProgress | None = None`

`MatchReport` gains the same two fields, plus:
- `metrics_only: bool = False` — set to `True` for imported matches (no coaching report)

---

## Section 2: History Import Pipeline

New module `dota_coach/importer.py` with an `import_match_metrics(account_id, limit)` async function.

**Steps:**
1. Fetch recent match IDs via OpenDota `GET /players/{account_id}/matches?limit={limit}`
2. Skip match IDs already in the history DB (using existing `get_analyzed_ids()`)
3. For each remaining match: fetch full match data from OpenDota via `GET /matches/{match_id}` (the `match_meta` dict passed to `enrich()` must include the full player list so bracket detection works). Run `extractor.extract_metrics()` + `enricher.enrich()` — same pipeline as `/analyze` but stop before the LLM call. Role detection follows the same two-step path as the full analysis: try Stratz positions first, fall back to `detect_role()`. If role cannot be determined, skip the match and count it as failed.
4. Save a `MatchReport` with `coaching_report=""`, `errors=[]`, `priority_focus=""`, `timeline=""`, `metrics_only=True`

Matches are processed sequentially to avoid hammering OpenDota. Individual failures are logged and counted, not fatal.

**API endpoint:** `POST /import-history/{account_id}?limit=50`
- Response: `{"imported": N, "skipped": M, "failed": K}`

**CLI:** new file `dota_coach/cli.py`. `pyproject.toml` already has `dota-coach = "dota_coach.cli:main"` — the new file must expose a `main()` entry point (e.g. a Typer `app` with `app()` called from `main()`). Do not change the `pyproject.toml` entry.

- Prints per-match progress and a final summary line

---

## Section 3: Local Benchmark Computation

Two changes to `history.py`:

**New function:**
```python
def get_local_benchmarks(
    account_id: int, hero: str, metrics: list[str]
) -> tuple[list[LocalBenchmark], int]:
    """Return (benchmarks, sample_size) for non-turbo matches on this hero.
    Returns ([], count) when count < 30."""
```

The stored `MatchReport` JSON has metrics nested under `$.metrics` as a `MatchMetrics` object. Field names inside `MatchMetrics` differ from benchmark metric names:

- `gold_per_min` → `$.metrics.gpm` (direct integer field)
- `xp_per_min` → `$.metrics.xpm` (direct integer field)
- `last_hits_per_min` → derived: fetch `$.metrics.total_last_hits` and `$.metrics.duration_minutes` separately, compute `total_last_hits / duration_minutes` in Python before percentile calculation

SQLite query for directly-stored metrics (`gpm`, `xpm`):
```sql
SELECT json_extract(report_json, '$.metrics.gpm') as val
FROM match_history
WHERE account_id = ?
  AND json_extract(report_json, '$.hero') = ?
  AND json_extract(report_json, '$.turbo') = 0
ORDER BY analyzed_at DESC LIMIT 500
```

For `last_hits_per_min`, fetch `total_last_hits` and `duration_minutes` in the same query and compute `total_last_hits / duration_minutes` in Python before percentile calculation.

The `turbo` filter uses `json_extract(...) = 0`. SQLite's `json_extract` returns JSON `false` as integer `0`, which is stable as long as the field is serialised as a JSON boolean (Python's `json.dumps(False)` → `false`). This is guaranteed by Pydantic's default serialisation of `bool` fields.

Percentile math is pure Python (sorted list + index interpolation, same approach as `_interpolate_pct` in `enricher.py`). No numpy dependency.

**Extended existing function:**
```python
def count_hero_matches(account_id: int, hero: str, turbo: bool | None = None) -> int:
```
When `turbo` is not None, adds `AND json_extract(report_json, '$.turbo') = ?` to the query. Progress indicator uses `turbo=False`.

---

## Section 4: Enrichment Integration

In `enricher.enrich()`, after the OpenDota/STRATZ benchmark block:

```python
LOCAL_THRESHOLD = 30
LOCAL_METRICS = ["gold_per_min", "xp_per_min", "last_hits_per_min"]

# Turbo matches skip local benchmark computation entirely
if not metrics.turbo:
    local_benchmarks, sample_size = get_local_benchmarks(account_id, metrics.hero, LOCAL_METRICS)
    if sample_size < LOCAL_THRESHOLD:
        local_benchmark_progress = LocalBenchmarkProgress(
            hero=metrics.hero,
            matches_stored=sample_size,
            threshold=LOCAL_THRESHOLD,
        )
        local_benchmarks = []
    else:
        local_benchmark_progress = None
else:
    local_benchmarks = []
    local_benchmark_progress = None
```

`enrich()` signature gains `account_id: int` parameter. **Both** call sites must be updated: the existing `/analyze` endpoint in `api.py` and the new import pipeline in `importer.py`. The `account_id` is available at both sites.

`get_local_benchmarks()` is a synchronous SQLite call invoked from the async `enrich()`. This follows the same accepted pattern as existing `history.py` functions (`save_match_report`, etc.) which are also called from async paths without `asyncio.to_thread`. The DB operations are fast enough for local SQLite that this is not a concern in practice.

Turbo accumulation is a separate issue (dota-analysis-ofa) — no local benchmarks or progress indicator for Turbo matches.

---

## Section 5: Detection + LLM Integration

**Detector (`detector.py`):**
When both OpenDota and local benchmarks are present for the same metric, produce a single merged `DetectedError` — not two separate errors. The rule fires if *either* source puts the metric below threshold. The merged `DetectedError.context` field carries both percentiles:
> `"43rd pct globally / 28th pct in your 45-game sample"`

When only one source is available (below threshold or metric absent in one source), the existing single-source error fires unchanged.

**LLM prompt (`prompt.py`):**
When local benchmarks are present, append a local benchmark block alongside the existing OpenDota block:
```
LOCAL BENCHMARKS (your last 45 non-turbo games on Anti-Mage):
  gold_per_min:      player=487  local_pct=61%  median=451  p25=398  p75=512
  xp_per_min:        player=612  local_pct=55%  median=590  p25=540  p75=650
  last_hits_per_min: player=6.2  local_pct=58%  median=5.9  p25=5.1  p75=6.8
```

When below threshold, append a single progress line:
```
LOCAL BENCHMARKS: 12/30 non-turbo Anti-Mage games stored — not enough for local percentiles yet.
```

---

## Section 6: UI

**Below threshold** — small note near the benchmark bars:
> *Local benchmarks: 12/30 Anti-Mage games stored — building your personal baseline*

**At/above threshold** — each benchmark bar shows both percentiles:
```
GPM  ████████░░  43rd pct (global)  |  61st pct (your 45 games)
```

**Imported matches in recent matches frame** — entries with `metrics_only: true`:
- Render with a visual distinction (e.g. subtle "metrics" badge or dimmed state)
- Chat input disabled or replaced with: *"Imported for benchmarks — no coaching report available."*

---

## Affected Files

| File | Change |
|---|---|
| `dota_coach/models.py` | Add `LocalBenchmark`, `LocalBenchmarkProgress`; extend `EnrichmentContext` and `MatchReport` |
| `dota_coach/history.py` | Add `get_local_benchmarks()`; extend `count_hero_matches(turbo=)` |
| `dota_coach/importer.py` | New module — `import_match_metrics()` |
| `dota_coach/enricher.py` | Call local benchmarks; add `account_id` param to `enrich()` |
| `dota_coach/detector.py` | Integrate local benchmarks into merged error detection |
| `dota_coach/prompt.py` | Local benchmark block in LLM context |
| `dota_coach/api.py` | `POST /import-history/{account_id}` endpoint; update **existing** `enrich()` call to pass `account_id` |
| `dota_coach/cli.py` | New file — `import-history` CLI command; register in `pyproject.toml` |
| `static/` | Progress indicator, side-by-side bars, `metrics_only` UI state |

---

## Out of Scope

- Turbo match local benchmarks (dota-analysis-ofa)
- Cross-session chat history (dota-analysis-7g8)
- Bracket-filtered benchmarks (STRATZ already handles bracket averages)
