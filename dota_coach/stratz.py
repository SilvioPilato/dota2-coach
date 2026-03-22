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
query HeroBracketBenchmarks($heroId: Short!, $bracketIds: [RankBracketBasicEnum]) {
  heroStats {
    stats(heroId: $heroId, bracketBasicIds: $bracketIds) {
      goldPerMin { percentile value }
      xpPerMin { percentile value }
      lastHitsPerMin { percentile value }
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
) -> dict[str, list[dict]] | None:
    """Fetch bracket-filtered hero benchmarks from STRATZ heroStats.

    Returns a dict mapping metric name → list of {percentile, value} dicts,
    in the same format as OpenDota benchmarks result. Returns None on any error
    (caller should fall back to global OpenDota benchmarks).

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
                    "variables": {"heroId": hero_id, "bracketIds": [bracket]},
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "STRATZ_API",
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("STRATZ bracket benchmark request failed for hero %s: %s", hero_id, exc)
        return None

    try:
        stats_list = data["data"]["heroStats"]["stats"]
        if not stats_list:
            return None
        stats = stats_list[0]
    except (KeyError, TypeError, IndexError):
        logger.warning("Unexpected STRATZ benchmark response shape for hero %s", hero_id)
        return None

    # Normalise: STRATZ uses camelCase keys; map to OpenDota snake_case
    _KEY_MAP = {
        "goldPerMin": "gold_per_min",
        "xpPerMin": "xp_per_min",
        "lastHitsPerMin": "last_hits_per_min",
    }
    result: dict[str, list[dict]] = {}
    for stratz_key, odota_key in _KEY_MAP.items():
        pts = stats.get(stratz_key)
        if pts:
            # Normalise percentile to 0-1 range (STRATZ may return 0-100)
            normalised = [
                {"percentile": p["percentile"] / 100.0 if p["percentile"] > 1 else p["percentile"],
                 "value": p["value"]}
                for p in pts
            ]
            result[odota_key] = normalised

    return result if result else None


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
