"""Regression: Ollama small-model drift on Entity.kind ('organization', 'fund', etc.)
must be tolerated, not reject the whole enrichment.

This bug took down the enrich loop in prod — 45 consecutive failures — because the
canonical Literal["company","ticker","person","other"] rejected the model's
'organization' output, the EnrichResult validator raised, retry hit the same output,
EnrichError fired, and the loop made zero forward progress.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from whatsapp_archive.enrich.ollama import Entity, EnrichResult


@pytest.mark.parametrize("raw_kind,expected", [
    # Canonical values pass through unchanged.
    ("company", "company"),
    ("ticker", "ticker"),
    ("person", "person"),
    ("other", "other"),
    # The live-prod failure: 'organization' must map to 'company'.
    ("organization", "company"),
    ("Organization", "company"),  # case-insensitive
    ("ORGANISATION", "company"),  # British spelling
    # Plausible adjacent variants that map to 'company'.
    ("corp", "company"),
    ("Corporation", "company"),
    ("fund", "company"),
    ("ETF", "company"),
    ("Index", "company"),
    ("central bank", "company"),
    # Government/agency-ish → 'other'.
    ("government", "other"),
    ("agency", "other"),
    ("country", "other"),
    # Ticker aliases.
    ("symbol", "ticker"),
    ("stock", "ticker"),
    # Unknown kind → 'other' (fail-soft, not fail-hard).
    ("widget", "other"),
    ("xyzzy", "other"),
])
def test_kind_normalization(raw_kind: str, expected: str) -> None:
    e = Entity(name="Test", kind=raw_kind, mention_text="t")
    assert e.kind == expected


def test_enrich_result_with_drifty_entities_parses() -> None:
    """Full EnrichResult with 'organization' entities round-trips."""
    raw = {
        "summary": "Summary text.",
        "categories": ["finance"],
        "suggested_new_category": None,
        "entities": [
            {"name": "BlackRock", "kind": "organization", "mention_text": "BlackRock"},
            {"name": "$NDX", "kind": "ticker", "mention_text": "$NDX"},
            {"name": "Powell", "kind": "person", "mention_text": "Powell"},
            {"name": "The Fed", "kind": "central bank", "mention_text": "Fed"},
        ],
        "sentiment": "bullish",
    }
    result = EnrichResult.model_validate(raw)
    kinds = [e.kind for e in result.entities]
    assert kinds == ["company", "ticker", "person", "company"]


def test_truly_invalid_kind_does_not_crash_loop() -> None:
    """Non-string kinds still fail validation (this is fine — the retry path handles)."""
    with pytest.raises(ValidationError):
        Entity(name="X", kind=42, mention_text="x")  # type: ignore[arg-type]
