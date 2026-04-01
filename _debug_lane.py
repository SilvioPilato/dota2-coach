import asyncio, json
from dota_coach.opendota import get_match

async def main():
    data = await get_match(8751149025)
    steam_id = 76561198095469286 - 76561197960265728
    our = next(p for p in data["players"] if p.get("account_id") == steam_id)
    print("Our hero_id:", our.get("hero_id"), "lane:", our.get("lane"), "isRadiant:", our.get("isRadiant"))
    print()
    for p in data["players"]:
        hero_info = p.get("hero") or {}
        hero_name = hero_info.get("localized_name", "") if isinstance(hero_info, dict) else ""
        print(f"  hero_id={p.get('hero_id')} name={hero_name!r} lane={p.get('lane')} lane_role={p.get('lane_role')} isRadiant={p.get('isRadiant')}")

asyncio.run(main())
