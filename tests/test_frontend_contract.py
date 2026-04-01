"""Contract test: every field accessed via m.* in ROLE_CARDS must exist in MatchMetrics."""
from __future__ import annotations

import re
from pathlib import Path

from dota_coach.models import MatchMetrics

_INDEX_HTML = Path(__file__).parent.parent / "static" / "index.html"

_ROLE_CARDS_RE = re.compile(
    r"const ROLE_CARDS\s*=\s*\{(.+?)\};",
    re.DOTALL,
)


def _extract_role_cards_field_names() -> set[str]:
    """Return all bare field names accessed as m.<name> in ROLE_CARDS."""
    source = _INDEX_HTML.read_text(encoding="utf-8")
    m = _ROLE_CARDS_RE.search(source)
    assert m, "Could not find ROLE_CARDS block in index.html"
    block = m.group(1)
    return set(re.findall(r"\bm\.([A-Za-z_]\w*)\b", block))


def test_role_cards_fields_exist_in_match_metrics():
    """All m.field accesses in ROLE_CARDS must correspond to real MatchMetrics fields."""
    model_fields = set(MatchMetrics.model_fields)
    frontend_fields = _extract_role_cards_field_names()
    missing = frontend_fields - model_fields
    assert not missing, (
        f"ROLE_CARDS references fields not present in MatchMetrics: {sorted(missing)}\n"
        "Update the field name in index.html or add it to MatchMetrics."
    )
