"""Stratz GraphQL API client for accurate pos 1-5 role detection and bracket benchmarks."""
from __future__ import annotations

import logging
import os

import httpx

STRATZ_GRAPHQL_URL = "https://api.stratz.com/graphql"

_QUERY = """
query GetMatchPositions($matchId: Long!) {
  match(id: $matchId) {
    players {
      steamAccountId
      position
    }
  }
}
"""

# Mapping from rank tier (rank_tier // 10) to STRATZ bracketBasicIds enum value
# rank_tier values: 1=Herald, 2=Guardian, 3=Crusader, 4=Archon, 5=Legend, 6=Ancient, 7+=Divine/Immortal
_RANK_TO_STRATZ_BRACKET: dict[int, str] = {
    1: "HERALD_GUARDIAN",
    2: "HERALD_GUARDIAN",
    3: "CRUSADER_ARCHON",
    4: "CRUSADER_ARCHON",
    5: "LEGEND_ANCIENT",
    6: "LEGEND_ANCIENT",
    7: "DIVINE_IMMORTAL",
    8: "DIVINE_IMMORTAL",
}

_BRACKET_BENCHMARKS_QUERY = """
query HeroBracketBenchmarks($heroIds: [Short], $bracketIds: [RankBracketBasicEnum]) {
  heroStats {
    stats(heroIds: $heroIds, bracketBasicIds: $bracketIds) {
      cs
      xp
      matchCount
      time
    }
  }
}
"""

_ITEM_BOOTSTRAP_QUERY = """
query HeroItemBootstrap($heroId: Short!, $bracket: RankBracketBasicEnum!) {
  heroStats {
    itemBootPurchase(heroId: $heroId, bracketBasicIds: [$bracket]) {
      itemId
      winCount
      matchCount
      avgTime
    }
  }
}
"""

logger = logging.getLogger(__name__)


def rank_tier_to_stratz_bracket(rank_tier: int) -> str:
    """Map an OpenDota rank_tier value to a STRATZ RankBracketBasicEnum string."""
    rank_num = max(1, min(8, rank_tier // 10)) if rank_tier >= 10 else 1
    return _RANK_TO_STRATZ_BRACKET.get(rank_num, "LEGEND_ANCIENT")


async def get_hero_bracket_benchmarks(
    hero_id: int,
    bracket: str,
) -> dict[str, float] | None:
    """Fetch bracket-average stats from STRATZ heroStats for a given hero + bracket.

    Returns a dict of {metric_name: bracket_average} for use as bracket_avg in
    HeroBenchmark. Metrics use OpenDota naming conventions:
      "gold_per_min" → averaging goldPerMinute
      "last_hits_per_min" → cs (raw, not normalised — caller should use for comparison only)
    Returns None on any error; caller falls back to global OpenDota median.

    Args:
        hero_id: OpenDota/Dota2 hero ID
        bracket:  STRATZ RankBracketBasicEnum value, e.g. "LEGEND_ANCIENT"
    """
    api_key = os.environ.get("STRATZ_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                STRATZ_GRAPHQL_URL,
                json={
                    "query": _BRACKET_BENCHMARKS_QUERY,
                    "variables": {"heroIds": [hero_id], "bracketIds": [bracket]},
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "STRATZ_API",
                },
            )
            if not response.is_success:
                logger.warning(
                    "STRATZ bracket benchmark request failed for hero %s: HTTP %s — %s",
                    hero_id, response.status_code, response.text[:500],
                )
                return None
            data = response.json()
    except Exception as exc:
        logger.warning("STRATZ bracket benchmark request failed for hero %s: %s", hero_id, exc)
        return None

    # GraphQL can return 200 with errors[] — treat that as a soft failure too
    if "errors" in data:
        logger.warning(
            "STRATZ bracket benchmark GraphQL errors for hero %s: %s",
            hero_id, data["errors"],
        )
        return None

    try:
        stats_list = data["data"]["heroStats"]["stats"]
        if not stats_list:
            return None
        # Aggregate all returned rows using matchCount as weight
        total_matches = sum(s.get("matchCount") or 0 for s in stats_list)
        if total_matches == 0:
            return None

        def _wavg(field: str) -> float | None:
            vals = [(s.get(field) or 0.0, s.get("matchCount") or 0) for s in stats_list]
            weighted = sum(v * w for v, w in vals)
            return weighted / total_matches if total_matches else None

        cs_avg = _wavg("cs")
        xp_avg = _wavg("xp")
        time_avg = _wavg("time")  # average game duration in minutes

        result: dict[str, float] = {}
        # Derive per-minute rates from totals using average duration
        if cs_avg is not None and time_avg and time_avg > 0:
            result["last_hits_per_min"] = cs_avg / time_avg
        if xp_avg is not None and time_avg and time_avg > 0:
            result["xp_per_min"] = xp_avg / time_avg
        # goldPerMinute is None in STRATZ heroStats — no GPM bracket average available

        return result if result else None

    except (KeyError, TypeError) as exc:
        logger.warning("Unexpected STRATZ benchmark response shape for hero %s: %s", hero_id, exc)
        return None


async def get_hero_item_bootstrap(hero_id: int, bracket: str) -> list[dict]:
    """Fetch item bootstrap data from STRATZ for a given hero + bracket.

    Returns a list of dicts with keys itemId, winCount, matchCount, avgTime.
    Returns [] on any error or if STRATZ_API_KEY is not set.

    Args:
        hero_id: OpenDota/Dota2 hero ID
        bracket:  STRATZ RankBracketBasicEnum value, e.g. "LEGEND_ANCIENT"
    """
    api_key = os.environ.get("STRATZ_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                STRATZ_GRAPHQL_URL,
                json={
                    "query": _ITEM_BOOTSTRAP_QUERY,
                    "variables": {"heroId": hero_id, "bracket": bracket},
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "STRATZ_API",
                },
            )
            if not response.is_success:
                logger.warning(
                    "STRATZ item bootstrap request failed for hero %s: HTTP %s — %s",
                    hero_id, response.status_code, response.text[:500],
                )
                return []
            data = response.json()
    except Exception as exc:
        logger.warning("STRATZ item bootstrap request failed for hero %s: %s", hero_id, exc)
        return []

    if "errors" in data:
        logger.warning(
            "STRATZ item bootstrap GraphQL errors for hero %s: %s",
            hero_id, data["errors"],
        )
        return []

    try:
        result = data["data"]["heroStats"]["itemBootPurchase"]
    except (KeyError, TypeError) as exc:
        logger.warning("Unexpected STRATZ item bootstrap response shape for hero %s: %s", hero_id, exc)
        return []

    if result is None or not isinstance(result, list):
        return []

    return result


async def get_match_positions(match_id: int) -> dict[int, int] | None:
    """Return a mapping of account_id (32-bit) → position (1-5) using Stratz GraphQL.

    Returns None if STRATZ_API_KEY is not set, the request fails, or the match
    has no position data (e.g. too recent / not yet processed by Stratz).

    Position enum values from Stratz: POSITION_1 through POSITION_5.
    These are stripped and parsed as integers (1-5).
    """
    api_key = os.environ.get("STRATZ_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                STRATZ_GRAPHQL_URL,
                json={"query": _QUERY, "variables": {"matchId": match_id}},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "STRATZ_API",
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("Stratz API request failed for match %s: %s", match_id, exc)
        return None

    try:
        players = data["data"]["match"]["players"]
    except (KeyError, TypeError):
        logger.warning("Unexpected Stratz response shape for match %s", match_id)
        return None

    if not players:
        return None

    # Steam64 → Steam32 offset
    _STEAM64_BASE = 76561197960265728

    result: dict[int, int] = {}
    for p in players:
        steam_id = p.get("steamAccountId")
        position_str = p.get("position")
        if steam_id is None or not position_str:
            continue
        # Strip "POSITION_" prefix, e.g. "POSITION_1" → 1
        if not isinstance(position_str, str) or not position_str.startswith("POSITION_"):
            continue
        try:
            pos = int(position_str.removeprefix("POSITION_"))
        except ValueError:
            continue
        if pos < 1 or pos > 5:
            continue
        # Stratz uses Steam64 IDs; OpenDota uses 32-bit account_id
        account_id = steam_id - _STEAM64_BASE if steam_id > _STEAM64_BASE else steam_id
        result[account_id] = pos

    return result if result else None
