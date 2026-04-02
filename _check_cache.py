import json
from pathlib import Path

cache_dir = Path.home() / ".dota_coach" / "cache"
f = cache_dir / "analysis_8751132385_135203558_3.json"
data = json.loads(f.read_text())
m = data.get("metrics", {})
print("hero_name:", m.get("hero_name"))
print("lane_enemies:", m.get("lane_enemies"))
print("lane_allies:", m.get("lane_allies"))
print("lane_matchup_winrates:", m.get("lane_matchup_winrates"))
print("lane_ally_synergy_scores:", m.get("lane_ally_synergy_scores"))
