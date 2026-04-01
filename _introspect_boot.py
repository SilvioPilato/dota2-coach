import asyncio, os, httpx, json
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.environ.get("STRATZ_API_KEY", "").strip()
url = "https://api.stratz.com/graphql"
headers = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json", "User-Agent": "STRATZ_API"}
q = """
{
  __type(name: "HeroItemBootPurchaseType") {
    fields { name type { name kind } }
  }
}
"""
async def main():
    async with httpx.AsyncClient() as c:
        r = await c.post(url, json={"query": q}, headers=headers)
        print(json.dumps(r.json(), indent=2))
asyncio.run(main())
