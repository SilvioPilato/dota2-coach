"""Context enricher: fetches hero benchmarks + patch data, caches to disk."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from dota_coach.models import EnrichmentContext, HeroBenchmark
from dota_coach.opendota import get_benchmarks
from dota_coach.stratz import fetch_hero_ally_synergies

CACHE_DIR = Path.home() / ".dota_coach" / "cache"
BENCHMARKS_TTL = 3600 * 6       # 6 hours
ITEMS_TTL = 3600 * 24 * 7       # 7 days
HEROES_TTL = 3600 * 24 * 7      # 7 days
ITEM_TIMINGS_TTL = 3600 * 24    # 24 hours — updates with new patch data
BOOTSTRAP_TTL = 3600 * 24  # 24 hours

_DOTACONSTANTS_BASE = (
    "https://raw.githubusercontent.com/odota/dotaconstants/master/build"
)
_OPENDOTA_BASE = "https://api.opendota.com/api"

# Minimum cost (gold) for an item to be considered a "core" purchase.
# Artifact-quality items below this threshold are minor components, not build-defining.
_CORE_ITEM_MIN_COST = 2000


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _read_cache(name: str, ttl: int) -> dict | list | None:
    """Read a cached JSON file if it exists and is within TTL."""
    path = CACHE_DIR / name
    if not path.exists():
        return None
    try:
        stat = path.stat()
        if time.time() - stat.st_mtime > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(name: str, data: Any) -> None:
    _ensure_cache_dir()
    path = CACHE_DIR / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


async def _fetch_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _get_items_data() -> dict:
    """Fetch items.json from dotaconstants (cached 7 days)."""
    cached = _read_cache("items.json", ITEMS_TTL)
    if cached is not None:
        return cached
    data = await _fetch_json(f"{_DOTACONSTANTS_BASE}/items.json")
    _write_cache("items.json", data)
    return data


async def _get_heroes_data() -> dict:
    """Fetch heroes.json from dotaconstants (cached 7 days)."""
    cached = _read_cache("heroes.json", HEROES_TTL)
    if cached is not None:
        return cached
    data = await _fetch_json(f"{_DOTACONSTANTS_BASE}/heroes.json")
    _write_cache("heroes.json", data)
    return data


async def _get_benchmarks_cached(hero_id: int) -> dict:
    """Fetch benchmarks from OpenDota (cached 6 hours)."""
    cache_name = f"benchmarks_{hero_id}.json"
    cached = _read_cache(cache_name, BENCHMARKS_TTL)
    if cached is not None:
        return cached
    data = await get_benchmarks(hero_id)
    _write_cache(cache_name, data)
    return data


async def _get_item_timings_cached(hero_id: int) -> list:
    """Fetch per-hero item purchase timings from OpenDota (cached 24h).

    Returns a list of dicts: [{item, time (seconds), games, wins}, ...]
    sorted by ascending average purchase time.
    """
    cache_name = f"item_timings_{hero_id}.json"
    cached = _read_cache(cache_name, ITEM_TIMINGS_TTL)
    if cached is not None:
        return cached
    data = await _fetch_json(f"{_OPENDOTA_BASE}/heroes/{hero_id}/itemTimings")
    _write_cache(cache_name, data)
    return data


async def _get_bootstrap_cached(hero_id: int, bracket: str) -> list:
    """Fetch Stratz item bootstrap for a hero+bracket (cached 24h). Returns [] on any error."""
    cache_name = f"bootstrap_{hero_id}_{bracket}.json"
    cached = _read_cache(cache_name, BOOTSTRAP_TTL)
    if cached is not None:
        return cached
    from dota_coach.stratz import get_hero_item_bootstrap
    try:
        data = await get_hero_item_bootstrap(hero_id, bracket)
    except Exception:
        data = []
    _write_cache(cache_name, data)
    return data


def _resolve_bootstrap_entries(raw: list, items_data: dict) -> list:
    """Convert raw Stratz bootstrap dicts to ItemBootstrapEntry list.

    - Builds a reverse map: numeric item_id → dotaconstants short name
    - Filters to match_frequency > 0.15
    - Converts avgTime (seconds) → avg_time_minutes
    - Returns [] if raw is empty or total_matches is 0
    """
    from dota_coach.models import ItemBootstrapEntry

    if not raw:
        return []

    # Build id→name reverse map from items_data
    id_to_name: dict[int, str] = {}
    for key, info in items_data.items():
        if isinstance(info, dict) and "id" in info:
            try:
                id_to_name[int(info["id"])] = key
            except (ValueError, TypeError):
                pass

    total_matches = sum(entry.get("matchCount") or 0 for entry in raw)
    if total_matches == 0:
        return []

    result = []
    for entry in raw:
        item_id = entry.get("itemId")
        match_count = entry.get("matchCount") or 0
        win_count = entry.get("winCount") or 0
        avg_time = entry.get("avgTime") or 0

        if not item_id or match_count == 0:
            continue

        item_name = id_to_name.get(int(item_id))
        if not item_name:
            continue  # unknown item, skip

        match_frequency = match_count / total_matches
        if match_frequency <= 0.15:
            continue  # below threshold

        result.append(ItemBootstrapEntry(
            item_id=int(item_id),
            item_name=item_name,
            match_frequency=match_frequency,
            win_rate=win_count / match_count,
            avg_time_minutes=(avg_time or 0) / 60.0,
        ))

    return result


def build_dynamic_core_items(items_data: dict) -> frozenset:
    """Derive the set of core items from dotaconstants items.json.

    Selects artifact-quality items that cost at least _CORE_ITEM_MIN_COST gold.
    Returns parser-style names (e.g. ``item_bfury``, ``item_radiance``).
    """
    return frozenset(
        f"item_{key}"
        for key, info in items_data.items()
        if isinstance(info, dict)
        and info.get("qual") == "artifact"
        and info.get("cost", 0) >= _CORE_ITEM_MIN_COST
    )


async def get_core_items() -> frozenset:
    """Return live core items derived from dotaconstants, with disk caching.

    Call this before ``extract_metrics`` to get a patch-current item set.
    Falls back to the static ``detector.CORE_ITEMS`` frozenset on any error.
    """
    try:
        items_data = await _get_items_data()
        result = build_dynamic_core_items(items_data)
        if result:
            return result
    except Exception:
        pass
    from dota_coach.detector import CORE_ITEMS
    return CORE_ITEMS


def _find_hero_id(hero_name: str, heroes_data: dict) -> int | None:
    """Find hero_id by matching hero name variants against heroes_data."""
    for hid, info in heroes_data.items():
        localized = info.get("localized_name", "")
        internal = info.get("name", "")
        # Match CamelCase unit name (e.g. "DrowRanger" → "Drow Ranger")
        if localized.replace(" ", "").lower() == hero_name.lower():
            return int(hid)
        if localized.lower() == hero_name.lower():
            return int(hid)
        # Match npc name
        if internal.lower() == f"npc_dota_hero_{hero_name.lower()}":
            return int(hid)
    return None


def _interpolate_pct(benchmarks_result: list[dict], player_value: float) -> float:
    """Interpolate player's percentile from benchmark distribution.

    benchmarks_result is a list of {percentile, value} dicts sorted by percentile.
    Returns 0.0–1.0 float.
    """
    if not benchmarks_result:
        return 0.5  # no data → assume median
    # Sort ascending by percentile
    pts = sorted(benchmarks_result, key=lambda x: x["percentile"])
    # Below minimum
    if player_value <= pts[0]["value"]:
        return pts[0]["percentile"]
    # Above maximum
    if player_value >= pts[-1]["value"]:
        return pts[-1]["percentile"]
    # Linear interpolation between bracketing points
    for i in range(len(pts) - 1):
        lo, hi = pts[i], pts[i + 1]
        if lo["value"] <= player_value <= hi["value"]:
            span = hi["value"] - lo["value"]
            if span == 0:
                return hi["percentile"]
            frac = (player_value - lo["value"]) / span
            return lo["percentile"] + frac * (hi["percentile"] - lo["percentile"])
    return pts[-1]["percentile"]


def _median_from_benchmarks(benchmarks_result: list[dict]) -> float:
    """Get approximate median (50th pct) value from benchmark data."""
    if not benchmarks_result:
        return 0.0
    pts = sorted(benchmarks_result, key=lambda x: x["percentile"])
    # Find closest to 0.5
    closest = min(pts, key=lambda x: abs(x["percentile"] - 0.5))
    return closest["value"]


async def enrich_lane_synergy(
    metrics: Any,
    bracket: str,
    heroes_data: dict,
) -> None:
    """Fetch and attach Stratz lane ally synergy data to metrics (mutates in place).

    Resolves ally hero names from metrics.lane_allies to hero IDs, queries
    fetch_hero_ally_synergies, and stores results in metrics.lane_ally_synergies
    and metrics.lane_ally_synergy_scores.

    Args:
        metrics: MatchMetrics instance to mutate.
        bracket: STRATZ RankBracketBasicEnum value (e.g. "LEGEND_ANCIENT").
        heroes_data: Heroes data dict from dotaconstants heroes.json.
    """
    hero_id = _find_hero_id(metrics.hero, heroes_data)
    if hero_id is None:
        return

    ally_ids = [
        id_
        for name in metrics.lane_allies
        if (id_ := _find_hero_id(name, heroes_data)) is not None
    ]
    if not ally_ids:
        return

    wr_by_id, syn_by_id = await fetch_hero_ally_synergies(hero_id, ally_ids, bracket)

    id_to_name = {
        hid: n for n in metrics.lane_allies if (hid := _find_hero_id(n, heroes_data)) is not None
    }
    metrics.lane_ally_synergies = {
        id_to_name[hid]: wr
        for hid, wr in wr_by_id.items()
        if hid in id_to_name
    }
    metrics.lane_ally_synergy_scores = {
        id_to_name[hid]: s
        for hid, s in syn_by_id.items()
        if hid in id_to_name
    }


async def enrich(
    metrics: Any,
    match_meta: dict,
    purchased_items: list[str] | None = None,
) -> EnrichmentContext:
    """Build enrichment context for a match.

    Args:
        metrics: MatchMetrics with hero, gpm, xpm fields
        match_meta: OpenDota match metadata dict
        purchased_items: list of item internal names purchased (e.g. ["item_battle_fury"])
    """
    from dota_coach.stratz import get_hero_bracket_benchmarks, rank_tier_to_stratz_bracket

    heroes_data = await _get_heroes_data()
    hero_id = _find_hero_id(metrics.hero, heroes_data)

    # Determine player's bracket from their rank_tier in match_meta
    our_player = next(
        (p for p in match_meta.get("players", []) if p.get("account_id") is not None),
        None,
    )
    rank_tier: int = (our_player or {}).get("rank_tier") or 0
    bracket = rank_tier_to_stratz_bracket(rank_tier) if rank_tier else "LEGEND_ANCIENT"

    benchmarks_list: list[HeroBenchmark] = []
    bracket_source = "global"
    if hero_id is not None:
        # Always fetch OpenDota global benchmarks for percentile ranking
        bench_data = await _get_benchmarks_cached(hero_id)
        bench_result = bench_data.get("result", {})

        # Optionally overlay bracket averages from STRATZ for a more relevant bracket_avg
        stratz_avgs = await get_hero_bracket_benchmarks(hero_id, bracket)
        if stratz_avgs:
            bracket_source = f"stratz_{bracket.lower()}"

        # Map metric names to (benchmark_key, player_value) pairs
        metric_map = {
            "gold_per_min": metrics.gpm,
            "xp_per_min": metrics.xpm,
        }
        # last_hits_per_min: total game LH / duration (matches OpenDota's benchmark definition)
        if metrics.duration_minutes > 0 and metrics.total_last_hits > 0:
            metric_map["last_hits_per_min"] = metrics.total_last_hits / metrics.duration_minutes

        for bench_key, player_val in metric_map.items():
            if bench_key in bench_result:
                pct = _interpolate_pct(bench_result[bench_key], player_val)
                # Use STRATZ bracket average if available, otherwise fall back to OpenDota global median
                bracket_avg = (
                    stratz_avgs[bench_key]
                    if stratz_avgs and bench_key in stratz_avgs
                    else _median_from_benchmarks(bench_result[bench_key])
                )
                benchmarks_list.append(
                    HeroBenchmark(
                        metric=bench_key,
                        player_value=float(player_val),
                        player_pct=pct,
                        bracket_avg=bracket_avg,
                    )
                )

    # Fetch item timings for this hero (used for live timing benchmarks in detector)
    item_timings: list = []
    if hero_id is not None:
        try:
            item_timings = await _get_item_timings_cached(hero_id)
        except Exception:
            item_timings = []

    # Fetch Stratz item bootstrap for hero-aware timing targets
    hero_item_bootstrap: list = []
    if hero_id is not None:
        try:
            raw_bootstrap = await _get_bootstrap_cached(hero_id, bracket)
            # items_data is fetched below; need it here too — reuse same call
            # Note: we fetch items_data early here; the later fetch will hit cache
            _items_for_bootstrap = await _get_items_data()
            hero_item_bootstrap = _resolve_bootstrap_entries(raw_bootstrap, _items_for_bootstrap)
        except Exception:
            hero_item_bootstrap = []

    # Fetch items data
    items_data = await _get_items_data()
    item_costs: dict[str, int] = {}
    if purchased_items:
        for item_name in purchased_items:
            # items.json keys are without "item_" prefix in some builds, try both
            key = item_name
            if key in items_data:
                item_costs[item_name] = items_data[key].get("cost", 0)
            elif item_name.replace("item_", "") in items_data:
                item_costs[item_name] = items_data[item_name.replace("item_", "")].get("cost", 0)

    # Hero base stats
    hero_base_stats: dict[str, float] = {}
    if hero_id is not None and str(hero_id) in heroes_data:
        hero_info = heroes_data[str(hero_id)]
        for stat_key in ("base_attack_min", "base_attack_max", "move_speed", "attack_range", "base_armor"):
            if stat_key in hero_info:
                hero_base_stats[stat_key] = float(hero_info[stat_key])

    # Patch version from match_meta
    patch_name = str(match_meta.get("patch", ""))

    return EnrichmentContext(
        patch_name=patch_name,
        benchmarks=benchmarks_list,
        item_costs=item_costs,
        hero_base_stats=hero_base_stats,
        bracket_source=bracket_source,
        item_timings=item_timings,
        hero_item_bootstrap=hero_item_bootstrap,
    )
