"""Quick verification that lane discovery works for a specific match."""
import asyncio
from dota_coach.opendota import get_match
from dota_coach.enricher import _discover_lane_heroes, enrich_lane_matchup, _get_heroes_data
from dota_coach.extractor import extract_metrics_from_opendota
from dota_coach.stratz import rank_tier_to_stratz_bracket

MATCH_ID = 8751149025
STEAM_ID = 76561198095469286
ACCOUNT_ID = STEAM_ID - 76561197960265728

async def main():
    print("Fetching match data...")
    match_meta = await get_match(MATCH_ID)
    heroes_data = await _get_heroes_data()

    metrics = extract_metrics_from_opendota(ACCOUNT_ID, match_meta)
    print(f"Hero: {metrics.hero}")
    print(f"lane_enemies BEFORE enrich: {metrics.lane_enemies}")
    print(f"lane_allies  BEFORE enrich: {metrics.lane_allies}")

    our_player = next(p for p in match_meta["players"] if p.get("account_id") == ACCOUNT_ID)
    bracket = rank_tier_to_stratz_bracket(our_player.get("rank_tier") or 0) or "LEGEND_ANCIENT"

    await enrich_lane_matchup(metrics, bracket, heroes_data, match_meta=match_meta, our_account_id=ACCOUNT_ID)
    print(f"\nlane_enemies AFTER  enrich: {metrics.lane_enemies}")
    print(f"lane_allies  AFTER  enrich: {metrics.lane_allies}")
    print(f"lane_matchup_winrates:      {metrics.lane_matchup_winrates}")
    print(f"lane_ally_synergy_scores:   {metrics.lane_ally_synergy_scores}")

asyncio.run(main())
