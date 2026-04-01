# Local Benchmark Accumulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After 30+ non-Turbo matches per hero are stored locally, compute p25/median/p75 from SQLite history and show them side-by-side with OpenDota global benchmarks in the UI, detection, and LLM context. A metrics-only import pipeline lets users bootstrap history without running the LLM.

**Architecture:** New `LocalBenchmark` / `LocalBenchmarkProgress` Pydantic models flow from `history.py` → `enricher.py` → `detector.py` + `prompt.py` → `MatchReport` → frontend. A new `importer.py` module runs the extract+enrich pipeline without the LLM step, exposed via `POST /import-history/{account_id}` and a Typer CLI.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, SQLite (`json_extract`), Typer (CLI), vanilla JS (frontend).

---

## File Map

| File | Action | Responsibility |
| ---- | ------ | -------------- |
| `dota_coach/models.py` | Modify | Add `LocalBenchmark`, `LocalBenchmarkProgress`; extend `EnrichmentContext` and `MatchReport` |
| `dota_coach/history.py` | Modify | Add `get_local_benchmarks()`; extend `count_hero_matches(turbo=)` |
| `dota_coach/enricher.py` | Modify | Add `account_id` param; integrate local benchmarks with turbo guard |
| `dota_coach/detector.py` | Modify | Merge local benchmarks into single `DetectedError` per metric |
| `dota_coach/prompt.py` | Modify | Append local benchmark block to `build_user_message()` |
| `dota_coach/api.py` | Modify | Update `enrich()` call; include local benchmarks in `MatchReport`; add `POST /import-history/{account_id}` |
| `dota_coach/importer.py` | Create | `import_match_metrics()` — extract+enrich without LLM |
| `dota_coach/cli.py` | Create | Typer app exposing `import-history` command via `main()` |
| `static/index.html` | Modify | Side-by-side benchmark bars; progress note; `metrics_only` sidebar state |
| `static/style.css` | Modify | CSS for local benchmark bar, progress note, metrics-only badge |
| `pyproject.toml` | Modify | Add `pytest-asyncio` to dev deps; add `[tool.pytest.ini_options]` |
| `tests/test_history.py` | Modify | Tests for `count_hero_matches(turbo=)` and `get_local_benchmarks()` |
| `tests/test_detector.py` | Modify | Tests for merged local+global error detection |
| `tests/test_prompt.py` | Modify | Tests for local benchmark block in `build_user_message()` |
| `tests/test_importer.py` | Create | Tests for `import_match_metrics()` |

---

### Task 1: Data Models

**Files:**
- Modify: `dota_coach/models.py`
- Test: `tests/test_history.py` (models used implicitly — no separate model tests needed)

- [ ] **Step 1: Add `LocalBenchmark` and `LocalBenchmarkProgress` models to `models.py`**

  After the `HeroBenchmark` class (around line 105), add:

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

- [ ] **Step 2: Extend `EnrichmentContext` with local benchmark fields**

  In `EnrichmentContext` (around line 122), add after `build_note`:

  ```python
  local_benchmarks: list["LocalBenchmark"] = []
  local_benchmark_progress: Optional["LocalBenchmarkProgress"] = None
  ```

  Add the forward references to the existing imports at the top (they're in the same file so no import needed — just make sure `LocalBenchmark` and `LocalBenchmarkProgress` are defined *before* `EnrichmentContext` in the file).

- [ ] **Step 3: Extend `MatchReport` with local benchmark fields and `metrics_only` flag**

  In `MatchReport` (around line 144), add after `timeline`:

  ```python
  local_benchmarks: list[LocalBenchmark] = []
  local_benchmark_progress: Optional[LocalBenchmarkProgress] = None
  metrics_only: bool = False
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add dota_coach/models.py
  git commit -m "feat: add LocalBenchmark/LocalBenchmarkProgress models and extend EnrichmentContext/MatchReport"
  ```

---

### Task 2: History DB — `count_hero_matches` turbo filter + `get_local_benchmarks`

**Files:**
- Modify: `dota_coach/history.py`
- Modify: `tests/test_history.py`

- [ ] **Step 1: Write failing tests for `count_hero_matches` turbo filter**

  Append to `tests/test_history.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Helpers for full-shape MatchReport JSON (needed for json_extract queries)
  # ---------------------------------------------------------------------------

  def _make_full_report(hero: str, gpm: int, xpm: int, total_lh: int,
                        duration: float, turbo: bool = False) -> dict:
      """Create a MatchReport-shaped dict that satisfies json_extract('$.metrics.*') queries."""
      return {
          "match_id": 999,
          "hero": hero,
          "turbo": turbo,
          "metrics": {
              "gpm": gpm,
              "xpm": xpm,
              "total_last_hits": total_lh,
              "duration_minutes": duration,
          },
      }


  class TestCountHeroMatchesTurboFilter:
      def test_turbo_false_excludes_turbo_matches(self, temp_db):
          """turbo=False should only count non-turbo matches."""
          r_normal = _make_full_report("Anti-Mage", 480, 600, 200, 35.0, turbo=False)
          r_turbo  = _make_full_report("Anti-Mage", 700, 900, 300, 22.0, turbo=True)
          save_match_report(100, 123, 1, r_normal)
          save_match_report(101, 123, 1, r_turbo)

          result = count_hero_matches(123, "Anti-Mage", turbo=False)
          assert result == 1

      def test_turbo_true_excludes_normal_matches(self, temp_db):
          """turbo=True should only count turbo matches."""
          r_normal = _make_full_report("Anti-Mage", 480, 600, 200, 35.0, turbo=False)
          r_turbo  = _make_full_report("Anti-Mage", 700, 900, 300, 22.0, turbo=True)
          save_match_report(100, 123, 1, r_normal)
          save_match_report(101, 123, 1, r_turbo)

          result = count_hero_matches(123, "Anti-Mage", turbo=True)
          assert result == 1

      def test_turbo_none_counts_all(self, temp_db):
          """turbo=None (default) should count all matches regardless of game mode."""
          r_normal = _make_full_report("Anti-Mage", 480, 600, 200, 35.0, turbo=False)
          r_turbo  = _make_full_report("Anti-Mage", 700, 900, 300, 22.0, turbo=True)
          save_match_report(100, 123, 1, r_normal)
          save_match_report(101, 123, 1, r_turbo)

          result = count_hero_matches(123, "Anti-Mage")
          assert result == 2
  ```

- [ ] **Step 2: Run tests — expect FAIL**

  ```
  pytest tests/test_history.py::TestCountHeroMatchesTurboFilter -v
  ```

  Expected: `FAILED — count_hero_matches() got unexpected keyword argument 'turbo'`

- [ ] **Step 3: Extend `count_hero_matches` in `history.py`**

  Replace the existing `count_hero_matches` function (around line 138):

  ```python
  def count_hero_matches(account_id: int, hero: str, turbo: bool | None = None) -> int:
      """Return the number of stored matches for a specific hero.

      Args:
          account_id: The account ID to filter by.
          hero: Hero name (must match json_extract('$.hero')).
          turbo: If True/False, filter to turbo/non-turbo matches. None counts all.
      """
      try:
          with _db() as conn:
              if turbo is None:
                  row = conn.execute(
                      """
                      SELECT COUNT(*) as cnt FROM match_history
                      WHERE account_id = ?
                        AND json_extract(report_json, '$.hero') = ?
                      """,
                      (account_id, hero),
                  ).fetchone()
              else:
                  row = conn.execute(
                      """
                      SELECT COUNT(*) as cnt FROM match_history
                      WHERE account_id = ?
                        AND json_extract(report_json, '$.hero') = ?
                        AND json_extract(report_json, '$.turbo') = ?
                      """,
                      (account_id, hero, 1 if turbo else 0),
                  ).fetchone()
          return int(row["cnt"]) if row else 0
      except Exception as exc:
          logger.warning("Failed to count hero matches for %s: %s", hero, exc)
          return 0
  ```

- [ ] **Step 4: Run tests — expect PASS**

  ```
  pytest tests/test_history.py::TestCountHeroMatchesTurboFilter -v
  ```

- [ ] **Step 5: Write failing tests for `get_local_benchmarks`**

  Add the import at the top of `tests/test_history.py`:

  ```python
  from dota_coach.history import get_local_benchmarks
  ```

  Append test class:

  ```python
  class TestGetLocalBenchmarks:
      def _save_n_matches(self, n: int, account_id: int, hero: str,
                          gpm: int = 480, xpm: int = 600,
                          total_lh: int = 200, duration: float = 35.0,
                          turbo: bool = False, db_fixture=None):
          """Helper: save n non-turbo matches for hero."""
          for i in range(n):
              r = _make_full_report(hero, gpm + i, xpm + i, total_lh + i, duration, turbo=turbo)
              save_match_report(1000 + i, account_id, 1, r)

      def test_below_threshold_returns_empty_list_and_count(self, temp_db):
          """With < 30 matches, returns ([], count)."""
          self._save_n_matches(12, 123, "Anti-Mage")

          benchmarks, count = get_local_benchmarks(123, "Anti-Mage",
                                                   ["gold_per_min", "xp_per_min"])
          assert benchmarks == []
          assert count == 12

      def test_above_threshold_returns_benchmarks(self, temp_db):
          """With >= 30 matches, returns populated LocalBenchmark list.

          _save_n_matches saves gpm 480, 481, ..., 514 (35 values, incrementing).
          Most recent match (first row DESC) has gpm=514 — the highest value —
          so player_pct should be close to 1.0 (top of the sample).
          """
          self._save_n_matches(35, 123, "Anti-Mage", gpm=480)

          benchmarks, count = get_local_benchmarks(123, "Anti-Mage", ["gold_per_min"])
          assert count == 35
          assert len(benchmarks) == 1
          b = benchmarks[0]
          assert b.metric == "gold_per_min"
          assert b.sample_size == 35
          assert b.p25 <= b.median <= b.p75
          # Most recent match has gpm=514, the highest in sample → player_pct > 0.9
          assert b.player_pct > 0.9

      def test_excludes_turbo_matches(self, temp_db):
          """Turbo matches are excluded even if hero matches."""
          self._save_n_matches(20, 123, "Anti-Mage", turbo=False)
          self._save_n_matches(20, 123, "Anti-Mage", turbo=True)

          benchmarks, count = get_local_benchmarks(123, "Anti-Mage", ["gold_per_min"])
          assert count == 20     # only non-turbo
          assert benchmarks == []  # still below threshold

      def test_last_hits_per_min_derived_correctly(self, temp_db):
          """last_hits_per_min is derived from total_last_hits / duration_minutes."""
          # 35 matches with known values: 210 LH / 35 min = 6.0 LH/min
          for i in range(35):
              r = _make_full_report("Anti-Mage", 480, 600, 210, 35.0, turbo=False)
              save_match_report(2000 + i, 123, 1, r)

          benchmarks, count = get_local_benchmarks(123, "Anti-Mage", ["last_hits_per_min"])
          assert count == 35
          assert len(benchmarks) == 1
          b = benchmarks[0]
          assert b.metric == "last_hits_per_min"
          assert abs(b.median - 6.0) < 0.01   # all same value → median == 6.0

      def test_hero_isolation(self, temp_db):
          """Only matches for the requested hero are counted."""
          self._save_n_matches(35, 123, "Anti-Mage")
          self._save_n_matches(35, 123, "Juggernaut")

          benchmarks_am, count_am = get_local_benchmarks(123, "Anti-Mage", ["gold_per_min"])
          assert count_am == 35
          benchmarks_jug, count_jug = get_local_benchmarks(123, "Juggernaut", ["gold_per_min"])
          assert count_jug == 35

      def test_empty_db_returns_zero_count(self, temp_db):
          """No stored matches returns ([], 0)."""
          benchmarks, count = get_local_benchmarks(999, "Anti-Mage", ["gold_per_min"])
          assert benchmarks == []
          assert count == 0
  ```

- [ ] **Step 6: Run tests — expect FAIL**

  ```
  pytest tests/test_history.py::TestGetLocalBenchmarks -v
  ```

  Expected: `ImportError: cannot import name 'get_local_benchmarks'`

- [ ] **Step 7: Implement `get_local_benchmarks` in `history.py`**

  Add at the end of `history.py`:

  ```python
  def _percentile_from_sorted(values: list[float], pct: float) -> float:
      """Interpolate a percentile from a sorted list of values (0.0-1.0 scale)."""
      if not values:
          return 0.0
      n = len(values)
      idx = pct * (n - 1)
      lo, hi = int(idx), min(int(idx) + 1, n - 1)
      frac = idx - lo
      return values[lo] + frac * (values[hi] - values[lo])


  def _player_pct_from_sorted(values: list[float], player_value: float) -> float:
      """Return the fraction of values strictly below player_value."""
      if not values:
          return 0.0
      below = sum(1 for v in values if v < player_value)
      return below / len(values)


  _LOCAL_THRESHOLD = 30
  _METRIC_QUERY: dict[str, tuple[str, str | None]] = {
      # metric_name -> (primary_json_path, secondary_json_path_or_None)
      # secondary is used only for derived metrics (last_hits_per_min)
      "gold_per_min":     ("$.metrics.gpm",              None),
      "xp_per_min":       ("$.metrics.xpm",              None),
      "last_hits_per_min": ("$.metrics.total_last_hits", "$.metrics.duration_minutes"),
  }


  def get_local_benchmarks(
      account_id: int, hero: str, metrics: list[str]
  ) -> tuple[list, int]:
      """Return (list[LocalBenchmark], sample_size) for non-turbo matches on this hero.

      Returns ([], count) when count < _LOCAL_THRESHOLD.
      The player_value used for percentile ranking is the most recent match's value.

      Note: LocalBenchmark is imported lazily to avoid circular imports.
      """
      from dota_coach.models import LocalBenchmark

      try:
          with _db() as conn:
              # Count non-turbo matches for this hero
              count_row = conn.execute(
                  """
                  SELECT COUNT(*) as cnt FROM match_history
                  WHERE account_id = ?
                    AND json_extract(report_json, '$.hero') = ?
                    AND json_extract(report_json, '$.turbo') = 0
                  """,
                  (account_id, hero),
              ).fetchone()
              count = int(count_row["cnt"]) if count_row else 0

              if count < _LOCAL_THRESHOLD:
                  return [], count

              result: list[LocalBenchmark] = []
              for metric in metrics:
                  if metric not in _METRIC_QUERY:
                      continue
                  primary_path, secondary_path = _METRIC_QUERY[metric]

                  if secondary_path is None:
                      # Direct field
                      rows = conn.execute(
                          f"""
                          SELECT json_extract(report_json, '{primary_path}') as val
                          FROM match_history
                          WHERE account_id = ?
                            AND json_extract(report_json, '$.hero') = ?
                            AND json_extract(report_json, '$.turbo') = 0
                            AND json_extract(report_json, '{primary_path}') IS NOT NULL
                          ORDER BY analyzed_at DESC LIMIT 500
                          """,
                          (account_id, hero),
                      ).fetchall()
                      values = [float(r["val"]) for r in rows if r["val"] is not None]
                  else:
                      # Derived: primary / secondary
                      rows = conn.execute(
                          f"""
                          SELECT json_extract(report_json, '{primary_path}') as primary_val,
                                 json_extract(report_json, '{secondary_path}') as secondary_val
                          FROM match_history
                          WHERE account_id = ?
                            AND json_extract(report_json, '$.hero') = ?
                            AND json_extract(report_json, '$.turbo') = 0
                            AND json_extract(report_json, '{primary_path}') IS NOT NULL
                            AND json_extract(report_json, '{secondary_path}') > 0
                          ORDER BY analyzed_at DESC LIMIT 500
                          """,
                          (account_id, hero),
                      ).fetchall()
                      values = [
                          float(r["primary_val"]) / float(r["secondary_val"])
                          for r in rows
                          if r["primary_val"] is not None and r["secondary_val"]
                      ]

                  if not values:
                      continue

                  sorted_vals = sorted(values)
                  # player_value = most recent match (first row in DESC order)
                  player_value = values[0]
                  player_pct = _player_pct_from_sorted(sorted_vals, player_value)

                  result.append(LocalBenchmark(
                      metric=metric,
                      player_value=player_value,
                      player_pct=player_pct,
                      p25=_percentile_from_sorted(sorted_vals, 0.25),
                      median=_percentile_from_sorted(sorted_vals, 0.50),
                      p75=_percentile_from_sorted(sorted_vals, 0.75),
                      sample_size=len(sorted_vals),
                  ))

              return result, count

      except Exception as exc:
          logger.warning("Failed to compute local benchmarks for %s: %s", hero, exc)
          return [], 0
  ```

- [ ] **Step 8: Run all history tests — expect PASS**

  ```
  pytest tests/test_history.py -v
  ```

- [ ] **Step 9: Commit**

  ```bash
  git add dota_coach/history.py tests/test_history.py
  git commit -m "feat: add get_local_benchmarks and count_hero_matches turbo filter"
  ```

---

### Task 3: Enrichment Integration

**Files:**
- Modify: `dota_coach/enricher.py`
- Test: `tests/test_enricher.py`

- [ ] **Step 1: Write failing test for `enrich()` accepting `account_id`**

  Append to `tests/test_enricher.py`:

  ```python
  def test_enrich_accepts_account_id_param(monkeypatch):
      """enrich() must accept account_id without raising TypeError."""
      import asyncio
      from unittest.mock import AsyncMock, patch
      from dota_coach.enricher import enrich
      from dota_coach.models import MatchMetrics

      # Minimal MatchMetrics — only fields enrich() reads
      m = MatchMetrics(
          match_id=1, hero="Anti-Mage", duration_minutes=35.0,
          result="win", lh_at_10=60, denies_at_10=5, deaths_before_10=0,
          death_timestamps_laning=[], net_worth_at_10=8000, net_worth_at_20=16000,
          opponent_net_worth_at_10=7500, opponent_net_worth_at_20=15000,
          gpm=480, xpm=600, total_last_hits=200,
          first_core_item_minute=None, first_core_item_name=None,
          laning_heatmap_own_half_pct=0.4, ward_purchases=0,
          teamfight_participation_rate=0.6, teamfight_avg_damage_contribution=None,
          first_roshan_minute=None, first_tower_minute=None,
          turbo=False,
      )
      match_meta = {"players": [], "patch": "7.37"}

      with patch("dota_coach.enricher._get_heroes_data", new_callable=AsyncMock, return_value={}), \
           patch("dota_coach.enricher._get_benchmarks_cached", new_callable=AsyncMock, return_value={"result": {}}), \
           patch("dota_coach.enricher.get_hero_bracket_benchmarks", new_callable=AsyncMock, return_value={}), \
           patch("dota_coach.enricher._get_item_timings_cached", new_callable=AsyncMock, return_value=[]), \
           patch("dota_coach.enricher._get_bootstrap_cached", new_callable=AsyncMock, return_value=[]), \
           patch("dota_coach.enricher._get_items_data", new_callable=AsyncMock, return_value={}), \
           patch("dota_coach.history.get_local_benchmarks", return_value=([], 0)):
          ctx = asyncio.run(enrich(m, match_meta, account_id=123))

      assert ctx is not None
  ```

- [ ] **Step 2: Run test — expect FAIL**

  ```
  pytest tests/test_enricher.py::test_enrich_accepts_account_id_param -v
  ```

  Expected: `TypeError: enrich() got an unexpected keyword argument 'account_id'`

- [ ] **Step 3: Update `enricher.enrich()` signature and body**

  In `dota_coach/enricher.py`, change the function signature (around line 440):

  ```python
  async def enrich(
      metrics: Any,
      match_meta: dict,
      purchased_items: list[str] | None = None,
      account_id: int = 0,
  ) -> EnrichmentContext:
  ```

  Add the local benchmark block after the existing benchmark loop (after line ~502, before the item timings block). First, add imports at the top of the function body or at module level — use lazy import to avoid circular dependency:

  ```python
  # --- Local benchmark integration (non-turbo only) ---
  from dota_coach.history import get_local_benchmarks
  from dota_coach.models import LocalBenchmark, LocalBenchmarkProgress

  LOCAL_THRESHOLD = 30
  LOCAL_METRICS = ["gold_per_min", "xp_per_min", "last_hits_per_min"]

  local_benchmarks_list: list[LocalBenchmark] = []
  local_benchmark_progress: LocalBenchmarkProgress | None = None

  if not getattr(metrics, "turbo", False) and account_id:
      _local_results, _sample_size = get_local_benchmarks(
          account_id, metrics.hero, LOCAL_METRICS
      )
      if _sample_size < LOCAL_THRESHOLD:
          local_benchmark_progress = LocalBenchmarkProgress(
              hero=metrics.hero,
              matches_stored=_sample_size,
              threshold=LOCAL_THRESHOLD,
          )
      else:
          local_benchmarks_list = _local_results
  ```

  Then update the `return EnrichmentContext(...)` call at the bottom of `enrich()` to include the new fields:

  ```python
  return EnrichmentContext(
      patch_name=patch_name,
      benchmarks=benchmarks_list,
      item_costs=item_costs,
      hero_base_stats=hero_base_stats,
      bracket_source=bracket_source,
      item_timings=item_timings,
      hero_item_bootstrap=hero_item_bootstrap,
      local_benchmarks=local_benchmarks_list,
      local_benchmark_progress=local_benchmark_progress,
  )
  ```

- [ ] **Step 4: Update `enrich()` call site in `api.py` (line 289)**

  Change:
  ```python
  enrichment = await enrich(metrics, match_meta)
  ```
  To:
  ```python
  enrichment = await enrich(metrics, match_meta, account_id=account_id)
  ```

- [ ] **Step 5: Update `MatchReport` construction in `api.py` (around line 325) to include local benchmark fields**

  Add to the `MatchReport(...)` call:
  ```python
  local_benchmarks=enrichment.local_benchmarks,
  local_benchmark_progress=enrichment.local_benchmark_progress,
  ```

- [ ] **Step 6: Run test — expect PASS**

  ```
  pytest tests/test_enricher.py::test_enrich_accepts_account_id_param -v
  ```

- [ ] **Step 7: Run full test suite to check for regressions**

  ```
  pytest tests/ -v --tb=short
  ```

- [ ] **Step 8: Commit**

  ```bash
  git add dota_coach/enricher.py dota_coach/api.py
  git commit -m "feat: integrate local benchmarks into enricher, add account_id param"
  ```

---

### Task 4: Detector — Merged Error Detection

**Files:**
- Modify: `dota_coach/detector.py`
- Modify: `tests/test_detector.py`

The existing v2 percentile rule loop (around line 323) fires a `DetectedError` per metric. We extend it to check both `enrichment.benchmarks` (global) and `enrichment.local_benchmarks` (local), producing one merged error when both sources are present for the same metric.

- [ ] **Step 1: Write failing tests**

  Append to `tests/test_detector.py`:

  ```python
  from dota_coach.models import LocalBenchmark, LocalBenchmarkProgress


  def _make_enrichment_with_local(global_pct: float, local_pct: float):
      """Build an EnrichmentContext with one global and one local benchmark for gold_per_min."""
      from dota_coach.models import EnrichmentContext, HeroBenchmark, LocalBenchmark
      return EnrichmentContext(
          patch_name="7.37",
          benchmarks=[
              HeroBenchmark(metric="gold_per_min", player_value=400.0,
                            player_pct=global_pct, bracket_avg=480.0)
          ],
          item_costs={},
          hero_base_stats={},
          local_benchmarks=[
              LocalBenchmark(metric="gold_per_min", player_value=400.0,
                             player_pct=local_pct, p25=350.0, median=480.0,
                             p75=560.0, sample_size=40)
          ],
      )


  class TestLocalBenchmarkDetection:
      def _base_metrics(self):
          from dota_coach.models import MatchMetrics
          return MatchMetrics(
              match_id=1, hero="Anti-Mage", duration_minutes=35.0, result="loss",
              lh_at_10=60, denies_at_10=5, deaths_before_10=0,
              death_timestamps_laning=[], net_worth_at_10=8000, net_worth_at_20=16000,
              opponent_net_worth_at_10=7500, opponent_net_worth_at_20=15000,
              gpm=400, xpm=550, total_last_hits=180,
              first_core_item_minute=None, first_core_item_name=None,
              laning_heatmap_own_half_pct=0.4, ward_purchases=0,
              teamfight_participation_rate=0.6, teamfight_avg_damage_contribution=None,
              first_roshan_minute=None, first_tower_minute=None, turbo=False,
          )

      def _base_role_profile(self):
          from dota_coach.models import RoleProfile
          return RoleProfile(
              observed_metrics=["gpm"],
              death_limit_before_10=2,
              tf_participation_limit=0.4,
              ward_rule="flag_if_laning_phase",
          )

      def test_both_sources_below_threshold_produces_single_merged_error(self):
          """When both global and local percentiles are below threshold, one error is produced."""
          enr = _make_enrichment_with_local(global_pct=0.18, local_pct=0.22)
          errors = detect_errors(self._base_metrics(), self._base_role_profile(), enr)
          gpm_errors = [e for e in errors if "GPM" in e.category]
          assert len(gpm_errors) == 1
          # Context must mention both global and local
          assert "global" in gpm_errors[0].context.lower()
          assert "game" in gpm_errors[0].context.lower() or "sample" in gpm_errors[0].context.lower()

      def test_only_local_below_threshold_fires_error(self):
          """When global is fine but local is below threshold, error still fires."""
          enr = _make_enrichment_with_local(global_pct=0.52, local_pct=0.18)
          errors = detect_errors(self._base_metrics(), self._base_role_profile(), enr)
          gpm_errors = [e for e in errors if "GPM" in e.category]
          assert len(gpm_errors) == 1

      def test_both_above_threshold_no_error(self):
          """When both global and local are above threshold, no error fires."""
          enr = _make_enrichment_with_local(global_pct=0.55, local_pct=0.60)
          errors = detect_errors(self._base_metrics(), self._base_role_profile(), enr)
          gpm_errors = [e for e in errors if "GPM" in e.category]
          assert len(gpm_errors) == 0
  ```

- [ ] **Step 2: Run tests — expect FAIL**

  ```
  pytest tests/test_detector.py::TestLocalBenchmarkDetection -v
  ```

  Expected: 3 failures — merged error is not yet produced.

- [ ] **Step 3: Update the v2 percentile rule loop in `detector.py`**

  The existing loop (around line 323) builds errors from `enrichment.benchmarks`. Replace that block with a version that considers both sources:

  ```python
  if enrichment is not None and role_profile is not None and not metrics.turbo:
      observed = role_profile.observed_metrics
      global_benchmarks = enrichment.benchmarks
      local_benchmarks = getattr(enrichment, "local_benchmarks", [])

      for obs_metric in observed:
          bench_key = _METRIC_TO_BENCH.get(obs_metric)
          if bench_key is None:
              continue

          global_bench = _find_benchmark(global_benchmarks, bench_key)
          local_bench = next((b for b in local_benchmarks if b.metric == bench_key), None)

          # Determine worst severity across available sources
          global_sev = _pct_severity(global_bench.player_pct) if global_bench else None
          local_sev  = _pct_severity(local_bench.player_pct)  if local_bench  else None

          sev = None
          if global_sev and local_sev:
              # Both present — take the worse (lower index = worse)
              sev = global_sev if _SEVERITY_ORDER[global_sev] <= _SEVERITY_ORDER[local_sev] else local_sev
          elif global_sev:
              sev = global_sev
          elif local_sev:
              sev = local_sev

          if sev is None:
              continue  # both above all thresholds

          label = _METRIC_LABELS.get(bench_key, bench_key)

          # Build context string combining available sources
          context_parts = []
          if global_bench:
              context_parts.append(
                  f"{global_bench.player_pct:.0%} pct globally (median {global_bench.bracket_avg:.0f} {label})"
              )
          if local_bench:
              context_parts.append(
                  f"{local_bench.player_pct:.0%} pct in your {local_bench.sample_size}-game sample"
              )
          context = " / ".join(context_parts) if context_parts else None

          # Use the worst-source percentile for player_pct display
          display_pct = min(
              p for p in [
                  global_bench.player_pct if global_bench else 1.0,
                  local_bench.player_pct  if local_bench  else 1.0,
              ]
          )

          errors.append(DetectedError(
              category=f"Low {label}",
              description=f"{label} is below expectations",
              severity=sev,
              metric_value=f"{(global_bench or local_bench).player_value:.0f} {label}",
              threshold=f"< {SEVERITY_THRESHOLDS[sev]:.0%} percentile",
              player_pct=display_pct,
              context=context,
          ))
  ```

  Also add `LocalBenchmark` to the model imports at the top of `detector.py`:
  ```python
  from dota_coach.models import (
      DeathCause,
      DetectedError,
      EnrichmentContext,
      HeroBenchmark,
      LocalBenchmark,
      MatchMetrics,
      RoleProfile,
  )
  ```

- [ ] **Step 4: Run tests — expect PASS**

  ```
  pytest tests/test_detector.py -v
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add dota_coach/detector.py tests/test_detector.py
  git commit -m "feat: merge local benchmarks into detector error rules"
  ```

---

### Task 5: Prompt — Local Benchmark Block

**Files:**
- Modify: `dota_coach/prompt.py`
- Modify: `tests/test_prompt.py`

- [ ] **Step 1: Write failing tests**

  Append to `tests/test_prompt.py`:

  ```python
  from dota_coach.models import LocalBenchmark, LocalBenchmarkProgress


  def _make_metrics_for_prompt():
      from dota_coach.models import MatchMetrics
      return MatchMetrics(
          match_id=1, hero="Anti-Mage", duration_minutes=35.0, result="loss",
          lh_at_10=60, denies_at_10=5, deaths_before_10=0,
          death_timestamps_laning=[], net_worth_at_10=8000, net_worth_at_20=16000,
          opponent_net_worth_at_10=7500, opponent_net_worth_at_20=15000,
          gpm=400, xpm=550, total_last_hits=180,
          first_core_item_minute=None, first_core_item_name=None,
          laning_heatmap_own_half_pct=0.4, ward_purchases=0,
          teamfight_participation_rate=0.6, teamfight_avg_damage_contribution=None,
          first_roshan_minute=None, first_tower_minute=None, turbo=False,
      )


  def _make_enrichment_with_local_benchmarks():
      from dota_coach.models import EnrichmentContext
      return EnrichmentContext(
          patch_name="7.37",
          benchmarks=[],
          item_costs={},
          hero_base_stats={},
          local_benchmarks=[
              LocalBenchmark(metric="gold_per_min", player_value=400.0,
                             player_pct=0.43, p25=350.0, median=480.0,
                             p75=560.0, sample_size=45),
          ],
      )


  def _make_enrichment_with_progress():
      from dota_coach.models import EnrichmentContext
      return EnrichmentContext(
          patch_name="7.37",
          benchmarks=[],
          item_costs={},
          hero_base_stats={},
          local_benchmark_progress=LocalBenchmarkProgress(
              hero="Anti-Mage", matches_stored=12, threshold=30
          ),
      )


  class TestLocalBenchmarkPrompt:
      def test_local_benchmark_block_appears_when_benchmarks_present(self):
          """build_user_message includes LOCAL BENCHMARKS block when local_benchmarks non-empty."""
          from dota_coach.prompt import build_user_message
          msg = build_user_message(
              _make_metrics_for_prompt(), [],
              role=1, enrichment=_make_enrichment_with_local_benchmarks()
          )
          assert "LOCAL BENCHMARKS" in msg
          assert "Anti-Mage" in msg
          assert "43%" in msg  # player_pct

      def test_progress_line_appears_when_below_threshold(self):
          """build_user_message includes progress line when local_benchmark_progress is set."""
          from dota_coach.prompt import build_user_message
          msg = build_user_message(
              _make_metrics_for_prompt(), [],
              role=1, enrichment=_make_enrichment_with_progress()
          )
          assert "LOCAL BENCHMARKS" in msg
          assert "12/30" in msg
          assert "Anti-Mage" in msg

      def test_no_local_benchmark_block_when_absent(self):
          """No LOCAL BENCHMARKS section when enrichment has neither local_benchmarks nor progress."""
          from dota_coach.prompt import build_user_message
          from dota_coach.models import EnrichmentContext
          enr = EnrichmentContext(patch_name="7.37", benchmarks=[], item_costs={}, hero_base_stats={})
          msg = build_user_message(_make_metrics_for_prompt(), [], role=1, enrichment=enr)
          assert "LOCAL BENCHMARKS" not in msg
  ```

- [ ] **Step 2: Run tests — expect FAIL**

  ```
  pytest tests/test_prompt.py::TestLocalBenchmarkPrompt -v
  ```

- [ ] **Step 3: Add local benchmark block to `build_user_message()` in `prompt.py`**

  At the end of `build_user_message()`, before the `return message` statement, add:

  ```python
  # --- LOCAL BENCHMARKS block ---
  local_benchmarks = getattr(enrichment, "local_benchmarks", []) if enrichment else []
  local_progress   = getattr(enrichment, "local_benchmark_progress", None) if enrichment else None

  if local_benchmarks and not metrics.turbo:
      lines.append("")
      sample_size = local_benchmarks[0].sample_size if local_benchmarks else 0
      lines.append(f"LOCAL BENCHMARKS (your last {sample_size} non-turbo games on {metrics.hero}):")
      for lb in local_benchmarks:
          lines.append(
              f"  {lb.metric}: player={lb.player_value:.0f}  "
              f"local_pct={lb.player_pct:.0%}  "
              f"median={lb.median:.0f}  p25={lb.p25:.0f}  p75={lb.p75:.0f}"
          )
  elif local_progress and not metrics.turbo:
      lines.append("")
      lines.append(
          f"LOCAL BENCHMARKS: {local_progress.matches_stored}/{local_progress.threshold} "
          f"non-turbo {local_progress.hero} games stored — "
          "not enough for local percentiles yet."
      )
  ```

  This must be inserted *before* the token budget check and `return message`.

- [ ] **Step 4: Run tests — expect PASS**

  ```
  pytest tests/test_prompt.py -v
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add dota_coach/prompt.py tests/test_prompt.py
  git commit -m "feat: add local benchmark block to LLM prompt"
  ```

---

### Task 5b: Set Up `pytest-asyncio` (prerequisite for async tests)

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Add `pytest-asyncio` to dev deps and configure asyncio mode**

  Edit `pyproject.toml`:

  ```toml
  [project.optional-dependencies]
  dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]

  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  ```

- [ ] **Step 2: Install the updated dev deps**

  ```bash
  pip install -e ".[dev]"
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add pyproject.toml
  git commit -m "chore: add pytest-asyncio dev dependency and set asyncio_mode=auto"
  ```

---

### Task 6: Importer Module

**Files:**
- Create: `dota_coach/importer.py`
- Create: `tests/test_importer.py`

- [ ] **Step 1: Write failing tests**

  Create `tests/test_importer.py`:

  ```python
  """Tests for dota_coach.importer — metrics-only match import pipeline."""
  from __future__ import annotations

  import gc
  import json
  import tempfile
  from pathlib import Path
  from unittest import mock
  from unittest.mock import AsyncMock, patch, MagicMock

  import pytest

  from dota_coach.history import _ensure_db, _db


  @pytest.fixture
  def temp_db():
      with tempfile.TemporaryDirectory() as tmpdir:
          temp_path = Path(tmpdir) / "test_import.db"
          with mock.patch("dota_coach.history.DB_PATH", temp_path):
              _ensure_db()
              yield temp_path
              gc.collect()


  def _fake_match_list():
      return [{"match_id": 1001}, {"match_id": 1002}]


  def _fake_match_meta(match_id: int, account_id: int) -> dict:
      return {
          "match_id": match_id,
          "game_mode": 1,
          "patch": "7.37",
          "players": [{"account_id": account_id, "hero_id": 1, "lane_role": 1,
                        "player_slot": 0, "rank_tier": 50}],
      }


  def _fake_metrics(match_id: int):
      from dota_coach.models import MatchMetrics
      return MatchMetrics(
          match_id=match_id, hero="Anti-Mage", duration_minutes=35.0, result="win",
          lh_at_10=60, denies_at_10=5, deaths_before_10=0,
          death_timestamps_laning=[], net_worth_at_10=8000, net_worth_at_20=16000,
          opponent_net_worth_at_10=7500, opponent_net_worth_at_20=15000,
          gpm=480, xpm=600, total_last_hits=200,
          first_core_item_minute=None, first_core_item_name=None,
          laning_heatmap_own_half_pct=0.4, ward_purchases=0,
          teamfight_participation_rate=0.6, teamfight_avg_damage_contribution=None,
          first_roshan_minute=None, first_tower_minute=None, turbo=False,
      )


  def _fake_enrichment():
      from dota_coach.models import EnrichmentContext
      return EnrichmentContext(patch_name="7.37", benchmarks=[], item_costs={}, hero_base_stats={})


  class TestImportMatchMetrics:
      @pytest.mark.asyncio
      async def test_imports_new_matches_and_saves_metrics_only(self, temp_db):
          """New matches are fetched, extracted, and saved with metrics_only=True."""
          account_id = 123

          with patch("dota_coach.history.DB_PATH", temp_db), \
               patch("dota_coach.importer.get_paginated_matches",
                     new_callable=AsyncMock, return_value=_fake_match_list()), \
               patch("dota_coach.importer.get_match",
                     new_callable=AsyncMock, side_effect=lambda mid: _fake_match_meta(mid, account_id)), \
               patch("dota_coach.importer.get_match_positions",
                     new_callable=AsyncMock, return_value={account_id: 1}), \
               patch("dota_coach.importer.extract_metrics_from_opendota",
                     return_value=_fake_metrics(1001)), \
               patch("dota_coach.importer.enrich",
                     new_callable=AsyncMock, return_value=_fake_enrichment()):
              from dota_coach.importer import import_match_metrics
              result = await import_match_metrics(account_id, limit=10)

          assert result["imported"] == 2
          assert result["skipped"] == 0
          assert result["failed"] == 0

          # Verify reports saved with metrics_only=True
          with mock.patch("dota_coach.history.DB_PATH", temp_db):
              with _db() as conn:
                  rows = conn.execute("SELECT report_json FROM match_history WHERE account_id=?",
                                      (account_id,)).fetchall()
          assert len(rows) == 2
          for row in rows:
              report = json.loads(row["report_json"])
              assert report["metrics_only"] is True
              assert report["coaching_report"] == ""

      @pytest.mark.asyncio
      async def test_skips_already_stored_matches(self, temp_db):
          """Matches already in the DB are skipped."""
          account_id = 123

          # Pre-store match 1001
          from dota_coach.history import save_match_report
          with patch("dota_coach.history.DB_PATH", temp_db):
              save_match_report(1001, account_id, 1, {"hero": "Anti-Mage", "turbo": False,
                                                       "metrics": {}})

          with patch("dota_coach.history.DB_PATH", temp_db), \
               patch("dota_coach.importer.get_paginated_matches",
                     new_callable=AsyncMock, return_value=_fake_match_list()), \
               patch("dota_coach.importer.get_match",
                     new_callable=AsyncMock, side_effect=lambda mid: _fake_match_meta(mid, account_id)), \
               patch("dota_coach.importer.get_match_positions",
                     new_callable=AsyncMock, return_value={account_id: 1}), \
               patch("dota_coach.importer.extract_metrics_from_opendota",
                     return_value=_fake_metrics(1002)), \
               patch("dota_coach.importer.enrich",
                     new_callable=AsyncMock, return_value=_fake_enrichment()):
              from dota_coach.importer import import_match_metrics
              result = await import_match_metrics(account_id, limit=10)

          assert result["imported"] == 1
          assert result["skipped"] == 1

      @pytest.mark.asyncio
      async def test_failed_match_does_not_abort_pipeline(self, temp_db):
          """A match that fails extraction is counted as failed, pipeline continues."""
          account_id = 123

          def raise_on_1001(mid):
              if mid == 1001:
                  raise ValueError("extraction failed")
              return _fake_match_meta(mid, account_id)

          with patch("dota_coach.history.DB_PATH", temp_db), \
               patch("dota_coach.importer.get_paginated_matches",
                     new_callable=AsyncMock, return_value=_fake_match_list()), \
               patch("dota_coach.importer.get_match",
                     new_callable=AsyncMock, side_effect=raise_on_1001), \
               patch("dota_coach.importer.get_match_positions",
                     new_callable=AsyncMock, return_value={account_id: 1}), \
               patch("dota_coach.importer.extract_metrics_from_opendota",
                     return_value=_fake_metrics(1002)), \
               patch("dota_coach.importer.enrich",
                     new_callable=AsyncMock, return_value=_fake_enrichment()):
              from dota_coach.importer import import_match_metrics
              result = await import_match_metrics(account_id, limit=10)

          assert result["failed"] == 1
          assert result["imported"] == 1

      @pytest.mark.asyncio
      async def test_undetermined_role_counts_as_failed(self, temp_db):
          """If role cannot be determined, match is skipped and counted as failed."""
          account_id = 123

          with patch("dota_coach.history.DB_PATH", temp_db), \
               patch("dota_coach.importer.get_paginated_matches",
                     new_callable=AsyncMock, return_value=[{"match_id": 1001}]), \
               patch("dota_coach.importer.get_match",
                     new_callable=AsyncMock, return_value=_fake_match_meta(1001, account_id)), \
               patch("dota_coach.importer.get_match_positions",
                     new_callable=AsyncMock, return_value={}), \
               patch("dota_coach.importer.detect_role",
                     side_effect=ValueError("cannot determine role")):
              from dota_coach.importer import import_match_metrics
              result = await import_match_metrics(account_id, limit=10)

          assert result["failed"] == 1
          assert result["imported"] == 0
  ```

- [ ] **Step 2: Run tests — expect FAIL**

  ```
  pytest tests/test_importer.py -v
  ```

  Expected: `ModuleNotFoundError: No module named 'dota_coach.importer'`

- [ ] **Step 3: Create `dota_coach/importer.py`**

  ```python
  """Metrics-only import pipeline.

  Fetches recent matches for an account, extracts metrics without running the LLM,
  and saves MatchReport(metrics_only=True) to the history DB. Used to bootstrap
  local benchmark data from past games.
  """
  from __future__ import annotations

  import logging

  from dota_coach.extractor import extract_metrics_from_opendota
  from dota_coach.enricher import enrich
  from dota_coach.history import get_analyzed_ids, save_match_report
  from dota_coach.models import MatchReport
  from dota_coach.opendota import get_match, get_paginated_matches
  from dota_coach.role import detect_role, ROLE_LABELS
  from dota_coach.stratz import get_match_positions

  logger = logging.getLogger(__name__)


  async def import_match_metrics(account_id: int, limit: int = 50) -> dict:
      """Fetch up to `limit` recent matches for `account_id`, extract metrics, save to DB.

      Returns:
          {"imported": int, "skipped": int, "failed": int}
      """
      imported = skipped = failed = 0

      # Fetch recent match list from OpenDota
      try:
          matches = await get_paginated_matches(account_id, limit=limit)
      except Exception as exc:
          logger.error("Failed to fetch match list for account %s: %s", account_id, exc)
          return {"imported": 0, "skipped": 0, "failed": 0}

      # Skip matches already stored
      stored_ids = get_analyzed_ids(account_id)
      to_process = [m for m in matches if m["match_id"] not in stored_ids]
      skipped = len(matches) - len(to_process)

      for match_stub in to_process:
          match_id = match_stub["match_id"]
          try:
              match_meta = await get_match(match_id)

              # Role detection: Stratz first, fallback to detect_role
              stratz_positions = await get_match_positions(match_id)
              if stratz_positions and account_id in stratz_positions:
                  role = stratz_positions[account_id]
              else:
                  role = detect_role(match_meta, account_id)

              role_label = ROLE_LABELS.get(role, "carry")

              metrics = extract_metrics_from_opendota(account_id, match_meta)
              enrichment = await enrich(metrics, match_meta, account_id=account_id)

              report = MatchReport(
                  match_id=match_id,
                  hero=metrics.hero,
                  role=role,
                  role_label=role_label,
                  result=metrics.result,
                  duration_minutes=metrics.duration_minutes,
                  patch=enrichment.patch_name,
                  turbo=metrics.turbo,
                  degraded=True,  # always degraded — no replay
                  metrics=metrics,
                  benchmarks=enrichment.benchmarks,
                  errors=[],
                  coaching_report="",
                  priority_focus="",
                  timeline="",
                  local_benchmarks=enrichment.local_benchmarks,
                  local_benchmark_progress=enrichment.local_benchmark_progress,
                  metrics_only=True,
              )

              save_match_report(match_id, account_id, role, report.model_dump())
              logger.info("Imported match %s for account %s (hero: %s)", match_id, account_id, metrics.hero)
              imported += 1

          except Exception as exc:
              logger.warning("Failed to import match %s: %s", match_id, exc)
              failed += 1

      return {"imported": imported, "skipped": skipped, "failed": failed}
  ```

- [ ] **Step 4: Run tests — expect PASS**

  ```
  pytest tests/test_importer.py -v
  ```

  Note: `pytest-asyncio` is needed. If not installed: `pip install pytest-asyncio`. Also add `asyncio_mode = "auto"` to `pyproject.toml` under `[tool.pytest.ini_options]` if not already present.

- [ ] **Step 5: Commit**

  ```bash
  git add dota_coach/importer.py tests/test_importer.py
  git commit -m "feat: add metrics-only import pipeline (importer.py)"
  ```

---

### Task 7: API Endpoint — `POST /import-history/{account_id}`

**Files:**
- Modify: `dota_coach/api.py`

No dedicated unit test — the endpoint is a thin wrapper over `import_match_metrics()` which is already tested. Verify with a smoke test after the endpoint is added.

- [ ] **Step 1: Add the endpoint to `api.py`**

  After the `GET /history/{account_id}` endpoint (around line 358), add:

  ```python
  # ---------------------------------------------------------------------------
  # POST /import-history/{account_id}
  # ---------------------------------------------------------------------------

  @app.post("/import-history/{account_id}")
  async def import_history(account_id: int, limit: int = 50):
      """Import recent matches for an account without running LLM analysis.

      Fetches up to `limit` matches, extracts metrics, and saves them to the
      history DB with metrics_only=True. Used to bootstrap local benchmark data.

      Returns: {"imported": N, "skipped": M, "failed": K}
      """
      _STEAM64_BASE = 76561197960265728
      if account_id > _STEAM64_BASE:
          account_id = account_id - _STEAM64_BASE

      from dota_coach.importer import import_match_metrics
      result = await import_match_metrics(account_id, limit=limit)
      return JSONResponse(content=result)
  ```

- [ ] **Step 2: Run the full test suite to check for regressions**

  ```
  pytest tests/ -v --tb=short
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add dota_coach/api.py
  git commit -m "feat: add POST /import-history/{account_id} endpoint"
  ```

---

### Task 8: CLI — `import-history` Command

**Files:**
- Create: `dota_coach/cli.py`

`pyproject.toml` already has `dota-coach = "dota_coach.cli:main"` — just create the file.

- [ ] **Step 1: Create `dota_coach/cli.py`**

  ```python
  """CLI entry point for dota-coach utilities.

  Registered as `dota-coach` in pyproject.toml [project.scripts].
  """
  from __future__ import annotations

  import asyncio
  import sys

  import typer

  app = typer.Typer(help="Dota 2 Coach CLI utilities.")


  @app.command()
  def import_history(
      account_id: int = typer.Argument(..., help="OpenDota account ID (Steam3 format)"),
      limit: int = typer.Option(50, help="Maximum number of recent matches to import"),
  ) -> None:
      """Import recent match metrics for ACCOUNT_ID without running LLM analysis.

      Saves metrics_only MatchReport records to the history DB so that local
      benchmark percentiles can be computed after 30+ matches per hero.
      """
      async def _run():
          from dota_coach.importer import import_match_metrics
          typer.echo(f"Importing up to {limit} matches for account {account_id}...")
          result = await import_match_metrics(account_id, limit=limit)
          typer.echo(
              f"Done. Imported: {result['imported']}  "
              f"Skipped: {result['skipped']}  "
              f"Failed: {result['failed']}"
          )

      asyncio.run(_run())
      # Note: asyncio.run() is correct here — the CLI is always a standalone
      # process, never invoked from within a running FastAPI event loop.


  def main() -> None:
      app()


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 2: Verify the CLI is importable and help works**

  ```bash
  python -m dota_coach.cli --help
  ```

  Expected: Typer help output listing the `import-history` command.

- [ ] **Step 3: Commit**

  ```bash
  git add dota_coach/cli.py
  git commit -m "feat: add CLI with import-history command (dota_coach/cli.py)"
  ```

---

### Task 9: UI — Benchmark Bars, Progress Note, `metrics_only` Sidebar State

**Files:**
- Modify: `static/index.html`

This task is vanilla JS + HTML; no unit tests. Verify visually in the browser.

- [ ] **Step 1: Update the benchmark rendering block in `renderReport()` (around line 425)**

  Replace the `else if (d.benchmarks && d.benchmarks.length)` block:

  ```javascript
  } else if (d.benchmarks && d.benchmarks.length) {
    // Build a lookup for local benchmarks by metric name
    const localByMetric = {};
    (d.local_benchmarks || []).forEach(lb => { localByMetric[lb.metric] = lb; });

    bch.innerHTML = d.benchmarks.map(b => {
      const globalPct = Math.round(b.player_pct * 100);
      const local = localByMetric[b.metric];
      const localPct = local ? Math.round(local.player_pct * 100) : null;

      const localBar = local
        ? `<div class="bench-bar bench-bar-local">
             <div class="bench-fill bench-fill-local" style="width:${localPct}%"></div>
           </div>
           <span class="bench-local-label">${localPct}% (your ${local.sample_size} games)</span>`
        : '';

      return `<div class="bench-row">
        <div class="bench-label">
          <span>${fmtBenchName(b.metric)}</span>
          <span>${globalPct}% global${local ? '' : ''}</span>
        </div>
        <div class="bench-bar"><div class="bench-fill" style="width:${globalPct}%"></div></div>
        ${localBar}
      </div>`;
    }).join('');

    // Progress note: show if local_benchmark_progress is set (below threshold)
    if (d.local_benchmark_progress) {
      const prog = d.local_benchmark_progress;
      const note = document.createElement('div');
      note.className = 'local-bench-note';
      note.textContent = `Local benchmarks: ${prog.matches_stored}/${prog.threshold} ${prog.hero} games stored — building your personal baseline`;
      bch.appendChild(note);
    }
  ```

- [ ] **Step 2: Update `buildMatchRow()` in `static/index.html` to show `metrics_only` badge**

  In `buildMatchRow(m)` (around line 743), after computing `stateClass`:

  ```javascript
  const metricsOnly = m.metrics_only === true;
  ```

  In the `row.innerHTML` template, inside `sidebar-badge-wrap`, add after the W/L badge:

  ```javascript
  ${metricsOnly ? '<span class="badge-metrics-only" title="Imported for benchmarks — no coaching report">metrics</span>' : ''}
  ```

  Update the `analyzed` click handler to short-circuit for `metrics_only` reports — show a message instead of loading the report:

  ```javascript
  if (analyzed && !expired) {
    row.addEventListener('click', async function(e) {
      if (e.target.classList.contains('sidebar-action')) return;
      // metrics_only: no coaching report to display
      if (metricsOnly) {
        // Optionally show a brief notice — for now just do nothing
        return;
      }
      // ... existing fetch/render logic unchanged ...
    });
  }
  ```

- [ ] **Step 3: Add CSS for new elements in `static/style.css`**

  Append to `static/style.css`:

  ```css
  /* Local benchmark secondary bar */
  .bench-bar-local {
    height: 4px;
    background: var(--bg-card);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 2px;
  }
  .bench-fill-local { background: var(--accent-secondary, #6c8ebf); height: 100%; border-radius: 2px; }
  .bench-local-label { font-size: 11px; color: var(--text-muted); }

  /* Local benchmark progress note */
  .local-bench-note {
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 8px;
    font-style: italic;
  }

  /* Metrics-only badge in sidebar */
  .badge-metrics-only {
    font-size: 10px;
    background: var(--bg-card);
    color: var(--text-muted);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 1px 4px;
    margin-left: 4px;
    vertical-align: middle;
  }
  ```

- [ ] **Step 4: Manual verification**

  Start the server:
  ```bash
  uvicorn dota_coach.api:app --reload --port 8000
  ```

  - Load a previously analyzed match and confirm benchmark bars still render correctly
  - Import a few matches via `POST /import-history/{account_id}` and confirm sidebar shows "metrics" badge
  - If 30+ non-turbo matches exist for a hero, confirm local percentile bar appears alongside global

- [ ] **Step 5: Commit**

  ```bash
  git add static/index.html static/style.css
  git commit -m "feat: add local benchmark bars, progress note, and metrics_only badge to UI"
  ```

---

### Task 10: Final — Run full suite, update beads, push

- [ ] **Step 1: Run the full test suite**

  ```bash
  pytest tests/ -v
  ```

  All tests must pass.

- [ ] **Step 2: Close the beads issue**

  ```bash
  bd close dota-analysis-5lc --reason="Implemented: LocalBenchmark/LocalBenchmarkProgress models, get_local_benchmarks in history.py, enricher integration, merged detector, prompt block, importer.py, CLI, API endpoint, UI bars"
  ```

- [ ] **Step 3: Push**

  ```bash
  git push
  ```
