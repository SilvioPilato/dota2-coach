"""Context enricher: fetches hero benchmarks + patch data, caches to disk."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from dota_coach.models import EnrichmentContext, HeroBenchmark
from dota_coach.opendota import get_benchmarks

CACHE_DIR = Path.home() / ".dota_coach" / "cache"
BENCHMARKS_TTL = 3600 * 6   # 6 hours
ITEMS_TTL = 3600 * 24 * 7   # 7 days
HEROES_TTL = 3600 * 24 * 7  # 7 days

_DOTACONSTANTS_BASE = (
    "https://raw.githubusercontent.com/odota/dotaconstants/master/build"
)


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
    path.write_text(json.dumps(data), encoding="utf-8")


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
        # Try STRATZ bracket-filtered benchmarks first (v3 feature)
        stratz_bench = await get_hero_bracket_benchmarks(hero_id, bracket)
        if stratz_bench is not None:
            bench_result = stratz_bench
            bracket_source = f"stratz_{bracket.lower()}"
        else:
            # Fall back to global OpenDota benchmarks
            bench_data = await _get_benchmarks_cached(hero_id)
            bench_result = bench_data.get("result", {})

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
                median = _median_from_benchmarks(bench_result[bench_key])
                benchmarks_list.append(
                    HeroBenchmark(
                        metric=bench_key,
                        player_value=float(player_val),
                        player_pct=pct,
                        bracket_avg=median,
                    )
                )

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
    )
