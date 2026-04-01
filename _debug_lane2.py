import asyncio, json
from dota_coach.opendota import get_match

async def main():
    data = await get_match(8751149025)
    steam_id = 76561198095469286 - 76561197960265728
    our = next(p for p in data["players"] if p.get("account_id") == steam_id)
    our_radiant = our.get("isRadiant")
    our_lane = our.get("lane")
    enemies = [p for p in data["players"] if p.get("isRadiant") != our_radiant]
    for p in enemies:
        print(f"hero_id={p.get('hero_id')} lane={p.get('lane')} hero field={p.get('hero')!r}")

asyncio.run(main())
