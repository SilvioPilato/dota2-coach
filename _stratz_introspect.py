"""Verify the corrected STRATZ bracket benchmarks function."""
import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from dota_coach.stratz import get_hero_bracket_benchmarks
    result = await get_hero_bracket_benchmarks(9, "LEGEND_ANCIENT")
    print("Result:", result)

asyncio.run(main())
