"""Stratz GraphQL API client for accurate pos 1-5 role detection."""
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

logger = logging.getLogger(__name__)


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
