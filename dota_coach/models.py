from __future__ import annotations

import enum
from typing import Literal, Optional

from pydantic import BaseModel


class LHEntry(BaseModel):
    minute: int
    last_hits: int
    denies: int


class DeathCause(str, enum.Enum):
    TEAMFIGHT = "TEAMFIGHT"
    DIVE = "DIVE"
    GANK_RUNE = "GANK_RUNE"
    NO_TP_RESPONSE = "NO_TP_RESPONSE"
    OVEREXTENSION = "OVEREXTENSION"
    UNKNOWN = "UNKNOWN"


class DeathEvent(BaseModel):
    time_minutes: float
    killer: str
    cause: DeathCause = DeathCause.UNKNOWN
    cause_detail: str = ""


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
    tower_damage: Optional[int] = None
    initiation_rate: Optional[float] = None

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


class ItemBootstrapEntry(BaseModel):
    """A single entry from Stratz heroItemBootstrap, filtered and resolved."""
    item_id: int
    item_name: str           # dotaconstants short name, e.g. "bfury", "radiance"
    match_frequency: float   # matchCount / total hero+bracket matches (0.0–1.0)
    win_rate: float          # winCount / matchCount
    avg_time_minutes: float  # avgTime from Stratz (seconds) converted to minutes


class EnrichmentContext(BaseModel):
    """External context injected into prompts by the enricher."""
    patch_name: str
    benchmarks: list[HeroBenchmark]
    item_costs: dict[str, int]           # item_name → gold cost
    hero_base_stats: dict[str, float]    # base_damage, base_armor, etc.
    bracket_source: str = "global"       # reserved for v3 bracket-filtered
    item_timings: list[dict] = []        # OpenDota /heroes/{id}/itemTimings — [{item, time, games, wins}]
    hero_item_bootstrap: list[ItemBootstrapEntry] = []
    build_note: Optional[str] = None     # set when first_core_item not found in bootstrap


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
    degraded: bool = False
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
    quote: str | None = None
