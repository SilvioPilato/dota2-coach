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
    total_last_hits: int = 0
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

    # All-role fields (v2) — None when source data absent
    ward_placements: Optional[int] = None
    stacks_created: Optional[int] = None
    hero_healing: Optional[int] = None
    deward_pct: Optional[float] = None
    stun_time: Optional[float] = None
    rune_control_pct: Optional[float] = None

    # Game mode
    turbo: bool = False


class RoleProfile(BaseModel):
    """Per-role configuration for metric observation and detection rules."""
    observed_metrics: list[str]
    death_limit_before_10: int
    tf_participation_limit: float
    ward_rule: Literal["flag_if_laning_phase", "none", "require_minimum"]


class HeroBenchmark(BaseModel):
    """A single benchmark metric from OpenDota /benchmarks."""
    metric: str              # e.g. "gold_per_min"
    player_value: float
    player_pct: float        # 0.0–1.0 percentile
    bracket_avg: float       # value at ~50th percentile


class EnrichmentContext(BaseModel):
    """External context injected into prompts by the enricher."""
    patch_name: str
    benchmarks: list[HeroBenchmark]
    item_costs: dict[str, int]           # item_name → gold cost
    hero_base_stats: dict[str, float]    # base_damage, base_armor, etc.
    bracket_source: str = "global"       # reserved for v3 bracket-filtered


class DetectedError(BaseModel):
    category: str
    description: str
    severity: Literal["critical", "high", "medium"]
    metric_value: str
    threshold: str
    player_pct: Optional[float] = None   # percentile (0–1), None for non-pct rules
    context: Optional[str] = None        # e.g. "global median for AM is 48 LH"


class MatchReport(BaseModel):
    """Full /analyze response shape — held by the browser for chat context."""
    match_id: int
    hero: str
    role: int
    role_label: str
    result: str
    duration_minutes: float
    patch: str = ""
    turbo: bool = False
    metrics: MatchMetrics
    benchmarks: list[HeroBenchmark] = []
    errors: list[DetectedError] = []
    coaching_report: str = ""
    priority_focus: str = ""
    timeline: str = ""


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    match_context: MatchReport
    history: list[ChatTurn] = []
    user_message: str
