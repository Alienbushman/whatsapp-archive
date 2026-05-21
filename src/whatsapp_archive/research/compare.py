"""R2 — Cross-entity comparison: divergence analysis across multiple entity dossiers."""
from __future__ import annotations

import difflib
import sqlite3


# ── Theme similarity ──────────────────────────────────────────────────────────

_STOP_WORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "at"}


def _themes_similar(t1: str, t2: str) -> bool:
    t1n, t2n = t1.lower().strip(), t2.lower().strip()
    if t1n == t2n:
        return True
    if difflib.SequenceMatcher(None, t1n, t2n).ratio() >= 0.6:
        return True
    words1 = set(t1n.split()) - _STOP_WORDS
    words2 = set(t2n.split()) - _STOP_WORDS
    if not words1 or not words2:
        return False
    overlap = len(words1 & words2)
    return overlap / min(len(words1), len(words2)) >= 0.5


# ── Sentiment helpers ─────────────────────────────────────────────────────────

def _majority_sentiment(s: dict) -> str:
    b = s.get("bullish", 0)
    br = s.get("bearish", 0)
    n = s.get("neutral", 0)
    if b > br and b > n:
        return "bullish"
    if br > b and br > n:
        return "bearish"
    return "neutral"


# ── Divergence detection ──────────────────────────────────────────────────────

def _detect_divergence(dossiers: list[dict]) -> list[dict]:
    if len(dossiers) < 2:
        return []

    names = [d["entity"]["name"] for d in dossiers]

    # Build timeline: {month: {entity_name: {article_count, bullish, bearish, neutral}}}
    timeline: dict[str, dict] = {}
    for d in dossiers:
        entity_name = d["entity"]["name"]
        for arc in d.get("narrative_arc", []):
            month = arc["month"]
            if month not in timeline:
                timeline[month] = {}
            timeline[month][entity_name] = {
                "article_count": arc["article_count"],
                **arc.get("sentiment", {}),
            }

    divergences: list[dict] = []

    for month in sorted(timeline):
        present = {n: timeline[month][n] for n in names if n in timeline[month]}
        if len(present) < 2:
            continue

        present_names = list(present.keys())
        for i in range(len(present_names)):
            for j in range(i + 1, len(present_names)):
                a_name = present_names[i]
                b_name = present_names[j]
                a = present[a_name]
                b = present[b_name]

                # Sentiment flip: both have enough data, and majority sentiments oppose
                if a["article_count"] >= 2 and b["article_count"] >= 2:
                    a_sent = _majority_sentiment(a)
                    b_sent = _majority_sentiment(b)
                    opposing = {("bullish", "bearish"), ("bearish", "bullish")}
                    if (a_sent, b_sent) in opposing:
                        a_score = (a.get("bullish", 0) - a.get("bearish", 0)) / max(a["article_count"], 1)
                        b_score = (b.get("bullish", 0) - b.get("bearish", 0)) / max(b["article_count"], 1)
                        divergences.append({
                            "month": month,
                            "type": "sentiment_flip",
                            "narrative": (
                                f"{a_name} sentiment turned {a_sent} ({a_score:+.1f}) "
                                f"while {b_name} was {b_sent} ({b_score:+.1f})"
                            ),
                            "article_ids": [
                                {"entity": a_name, "ids": []},
                                {"entity": b_name, "ids": []},
                            ],
                        })

                # Volume divergence: ratio > 3x
                a_count = a["article_count"]
                b_count = b["article_count"]
                if a_count > 0 and b_count > 0:
                    ratio = max(a_count, b_count) / min(a_count, b_count)
                    if ratio > 3:
                        more_name = a_name if a_count > b_count else b_name
                        less_name = b_name if a_count > b_count else a_name
                        more_count = max(a_count, b_count)
                        less_count = min(a_count, b_count)
                        divergences.append({
                            "month": month,
                            "type": "volume_divergence",
                            "narrative": (
                                f"{more_name} mentioned {more_count}× vs {less_count}× "
                                f"for {less_name} in {month}"
                            ),
                            "article_ids": [
                                {"entity": a_name, "ids": []},
                                {"entity": b_name, "ids": []},
                            ],
                        })

    return divergences[:20]


# ── Shared / unique themes ────────────────────────────────────────────────────

def _compute_themes(dossiers: list[dict]) -> tuple[list[dict], list[dict]]:
    all_themes: list[tuple[str, str, int]] = []
    for d in dossiers:
        for bucket in d.get("claim_buckets", []):
            theme = bucket.get("theme", "").strip()
            article_count = len(bucket.get("article_ids", []))
            if theme:
                all_themes.append((d["entity"]["name"], theme, article_count))

    if not all_themes:
        return [], []

    groups: list[list[tuple]] = []
    used: set[int] = set()

    for i, (en_i, theme_i, cnt_i) in enumerate(all_themes):
        if i in used:
            continue
        group: list[tuple] = [(en_i, theme_i, cnt_i)]
        used.add(i)
        for j, (en_j, theme_j, cnt_j) in enumerate(all_themes):
            if j in used or en_j == en_i:
                continue
            if _themes_similar(theme_i, theme_j):
                group.append((en_j, theme_j, cnt_j))
                used.add(j)
        groups.append(group)

    shared: list[dict] = []
    unique: list[dict] = []

    for group in groups:
        entities_present = list({en for en, _, _ in group})
        if len(entities_present) >= 2:
            shared.append({
                "theme": group[0][1],
                "entities_present": entities_present,
                "counts": {en: cnt for en, _, cnt in group},
            })
        else:
            en, theme, cnt = group[0]
            unique.append({"entity": en, "theme": theme, "article_count": cnt})

    return shared, unique


# ── Main entry ────────────────────────────────────────────────────────────────

def compare_entities(
    conn: sqlite3.Connection,
    entity_names: list[str],
    ollama_url: str,
) -> dict:
    """Build side-by-side comparison for given entities.

    Fetches (or builds) R1 dossiers for each entity, then derives divergence
    points and shared/unique themes.
    """
    from .dossier import build_entity_dossier

    dossiers: list[dict] = []
    not_found: list[str] = []
    for name in entity_names:
        d = build_entity_dossier(conn, name.strip(), ollama_url)
        if d:
            dossiers.append(d)
        else:
            not_found.append(name.strip())

    shared, unique = _compute_themes(dossiers)

    return {
        "entities": dossiers,
        "divergence_points": _detect_divergence(dossiers),
        "shared_themes": shared,
        "unique_themes": unique,
        "not_found": not_found,
    }
