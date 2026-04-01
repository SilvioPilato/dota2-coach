import asyncio
from dota_coach.enricher import _discover_lane_heroes, _get_heroes_data
from dota_coach.opendota import get_match
from dota_coach.models import MatchMetrics

async def main():
    match_meta = await get_match(8751149025)
    heroes_data = await _get_heroes_data()
    steam_id = 76561198095469286 - 76561197960265728

    # Simulate blank metrics as produced by extract_metrics
    m = MatchMetrics(
        match_id=8751149025, hero="EmberSpirit", duration_minutes=30, result="win",
        lh_at_10=46, denies_at_10=7, deaths_before_10=0, death_timestamps_laning=[],
        net_worth_at_10=0, net_worth_at_20=0, opponent_net_worth_at_10=0, opponent_net_worth_at_20=0,
        gpm=500, xpm=600, first_core_item_minute=None, first_core_item_name=None,
        laning_heatmap_own_half_pct=0.5, ward_purchases=0,
        teamfight_participation_rate=None, teamfight_avg_damage_contribution=None,
        first_roshan_minute=None, first_tower_minute=None,
    )

    _discover_lane_heroes(m, steam_id, match_meta, heroes_data)
    print("lane_allies:", m.lane_allies)
    print("lane_enemies:", m.lane_enemies)

asyncio.run(main())
