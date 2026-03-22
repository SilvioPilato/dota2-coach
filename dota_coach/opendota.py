"""
OpenDota API client (async, httpx).

Rate limit: 60 req/min unauthenticated. Single-match runs make ≤2 calls — no throttling needed.
v2 batch mode: add asyncio.Semaphore(1) + 1s delay between requests.
"""
import httpx

BASE_URL = "https://api.opendota.com/api"

# 64-bit Steam ID offset
_STEAM_ID_OFFSET = 76561197960265728


def extract_account_id(steam_id_64: int) -> int:
    """Convert 64-bit Steam ID to 32-bit OpenDota account_id."""
    return steam_id_64 - _STEAM_ID_OFFSET


async def get_match(match_id: int) -> dict:
    """GET /matches/{match_id} — returns full match JSON including replay_url and players[]."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{BASE_URL}/matches/{match_id}")
        response.raise_for_status()
        return response.json()


async def get_recent_matches(account_id: int, limit: int = 10) -> list[dict]:
    """GET /players/{account_id}/recentMatches — returns list of recent match summaries."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{BASE_URL}/players/{account_id}/recentMatches",
            params={"limit": limit},
        )
        response.raise_for_status()
        return response.json()


def identify_enemy_carry(match: dict, our_account_id: int) -> dict | None:
    """
    Find the enemy safe-lane player (lane_role == 1) on the opposing team.

    Known limitation: some carries play offlane or mid. For v1, lane_role == 1
    on the opposing team is used as the carry heuristic.

    Returns the player dict from match['players'], or None if not found.
    """
    our_player = next(
        (p for p in match["players"] if p.get("account_id") == our_account_id),
        None,
    )
    if our_player is None:
        return None

    our_team = "radiant" if our_player.get("isRadiant") else "dire"
    enemy_team_flag = not our_player.get("isRadiant")

    return next(
        (
            p
            for p in match["players"]
            if p.get("isRadiant") == enemy_team_flag and p.get("lane_role") == 1
        ),
        None,
    )


async def request_parse_and_wait(match_id: int, timeout: int = 90, interval: int = 5) -> str:
    """Trigger an OpenDota parse for match_id and poll until replay_url is available.

    Returns the replay URL once ready.
    Raises TimeoutError if the match isn't processed within `timeout` seconds.
    """
    import asyncio

    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.post(f"{BASE_URL}/request/{match_id}")

    elapsed = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval
            resp = await client.get(f"{BASE_URL}/matches/{match_id}")
            resp.raise_for_status()
            data = resp.json()
            replay_url = data.get("replay_url")
            if replay_url:
                return replay_url
            cluster = data.get("cluster")
            salt = data.get("replay_salt")
            if cluster and salt:
                return f"http://replay{cluster}.valve.net/570/{match_id}_{salt}.dem.bz2"

    raise TimeoutError(f"OpenDota did not process match {match_id} within {timeout}s")


async def get_benchmarks(hero_id: int) -> dict:
    """GET /benchmarks?hero_id={hero_id} — returns percentile benchmark data for a hero."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{BASE_URL}/benchmarks",
            params={"hero_id": hero_id},
        )
        response.raise_for_status()
        return response.json()


def get_hero_id(hero_name: str, heroes_data: dict) -> int | None:
    """Lookup hero_id from dotaconstants heroes.json data by localized_name or internal name.

    heroes_data is a dict keyed by hero_id (str) with values containing 'localized_name' and 'name'.
    Returns None if no match found.
    """
    for hid, info in heroes_data.items():
        if info.get("localized_name", "").lower() == hero_name.lower():
            return int(hid)
        # Also match internal npc name like "npc_dota_hero_anti_mage"
        if info.get("name", "").lower() == hero_name.lower():
            return int(hid)
    return None
