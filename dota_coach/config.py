"""Runtime configuration loaded from environment variables.

All values have sensible defaults matching the original hardcoded constants.
Override any value by setting the corresponding env var (in .env or shell).

Example .env:
    DOTA_COACH_TOKEN_BUDGET=1000
    DOTA_COACH_LH_AT_10_MIN=40
    DOTA_COACH_DEATH_LIMIT=3
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

TOKEN_BUDGET: int = _int("DOTA_COACH_TOKEN_BUDGET", 800)

# ---------------------------------------------------------------------------
# Error detection thresholds
# ---------------------------------------------------------------------------

# Rule 1: Poor laning CS
LH_AT_10_MIN: int = _int("DOTA_COACH_LH_AT_10_MIN", 45)

# Rule 2: Unsafe laning — deaths before 10 min
DEATH_LIMIT_BEFORE_10: int = _int("DOTA_COACH_DEATH_LIMIT", 2)

# Rule 3: Very early death cutoff (minutes)
EARLY_DEATH_MINUTES: float = _float("DOTA_COACH_EARLY_DEATH_MINUTES", 5.0)

# Rule 4: Slow first core item (minutes)
SLOW_CORE_ITEM_MINUTES: float = _float("DOTA_COACH_SLOW_CORE_ITEM_MINUTES", 18.0)

# Rule 5 & 6: Net worth deficits (gold)
NW_DEFICIT_AT_10: int = _int("DOTA_COACH_NW_DEFICIT_AT_10", 1000)
NW_DEFICIT_AT_20: int = _int("DOTA_COACH_NW_DEFICIT_AT_20", 2500)

# Rule 7: Passive laning — own-half positioning fraction (0–1)
PASSIVE_LANING_OWN_HALF_PCT: float = _float("DOTA_COACH_PASSIVE_LANING_PCT", 0.70)

# Rule 8: Carry buying wards — minimum ward purchases to flag
WARD_PURCHASE_LIMIT: int = _int("DOTA_COACH_WARD_PURCHASE_LIMIT", 2)

# Rule 9: Farming during fights — participation floor (0–1)
TF_PARTICIPATION_FLOOR: float = _float("DOTA_COACH_TF_PARTICIPATION_FLOOR", 0.40)
