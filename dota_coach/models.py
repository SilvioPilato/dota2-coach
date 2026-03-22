from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class LHEntry(BaseModel):
    minute: int
    last_hits: int
    denies: int


class DeathEvent(BaseModel):
    time_minutes: float
    killer: str


class TeamfightEntry(BaseModel):
    start_time_minutes: float
    participated: bool
    damage_dealt: int
    deaths: int


class MatchMetrics(BaseModel):
    match_id: int
    hero: str
    duration_minutes: float
    result: Literal["win", "loss"]

    # Laning
    lh_at_10: int
    denies_at_10: int
    deaths_before_10: int
    death_timestamps_laning: list[float]
    net_worth_at_10: int
    enemy_carry_net_worth_at_10: int
    net_worth_at_20: int
    enemy_carry_net_worth_at_20: int

    # Farming
    gpm: int
    xpm: int
    first_core_item_minute: Optional[float]
    first_core_item_name: Optional[str]

    # Positioning
    laning_heatmap_own_half_pct: float
    ward_purchases: int

    # Fighting
    teamfight_participation_rate: Optional[float]
    teamfight_avg_damage_contribution: Optional[float]

    # Objectives
    first_roshan_minute: Optional[float]
    first_tower_minute: Optional[float]


class DetectedError(BaseModel):
    category: str
    description: str
    severity: Literal["critical", "high", "medium"]
    metric_value: str
    threshold: str
