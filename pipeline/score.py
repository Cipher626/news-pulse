"""How hot is each story? Every weight comes from config.toml."""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

from .cluster import ClusterSettings, Story, jaccard
from .util import outlet_key


def phrase_regex(words: list[str]) -> re.Pattern | None:
    words = [w.strip().lower() for w in words if w.strip()]
    if not words:
        return None
    alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])", re.IGNORECASE)


class Categories:
    def __init__(self, entries: list[dict]):
        self.items = [
            (e["name"], e.get("label", e["name"].title()), float(e.get("weight", 1)), phrase_regex(e.get("keywords", [])))
            for e in entries
        ]

    def match(self, text: str) -> list[tuple[str, str, float]]:
        return [(name, label, weight) for name, label, weight, rx in self.items if rx and rx.search(text)]


def independent_sources(story: Story, wire_similarity: float) -> int:
    """Distinct outlets, where near-identical headlines (wire copy) count once."""
    reps: list[frozenset] = []
    seen: set[str] = set()
    for art in sorted(story.articles, key=lambda a: a.published):
        key = outlet_key(art.outlet)
        if key in seen:
            continue
        seen.add(key)
        if any(jaccard(art.tokens, rep) >= wire_similarity for rep in reps):
            continue
        reps.append(art.tokens)
    return len(reps)


def score_stories(stories: list[Story], cfg: dict, settings: ClusterSettings, now: dt.datetime) -> None:
    sc = cfg.get("scoring", {})
    fresh_minutes = sc.get("fresh_minutes", 90)
    fresh_weight = sc.get("fresh_weight", 0.5)
    global_multiplier = sc.get("global_multiplier", 0.7)
    decay_hours = sc.get("decay_hours", 6)
    hot_score = cfg.get("site", {}).get("hot_score", 6.0)

    categories = Categories(cfg.get("sensitive", []))
    india_rx = phrase_regex(cfg.get("india", {}).get("keywords", []))
    fresh_cut = now - dt.timedelta(minutes=fresh_minutes)

    for story in stories:
        text = " | ".join(a.title for a in story.articles)
        matched = categories.match(text)
        story.category_names = [name for name, _, _ in matched]
        story.categories = [label for _, label, _ in matched]
        sensitive = max((w for _, _, w in matched), default=0.0)

        story.independent = independent_sources(story, settings.wire_similarity)
        story.fresh = len({outlet_key(a.outlet) for a in story.articles if a.published >= fresh_cut})

        sections = Counter(a.section for a in story.articles)
        top = max(sections.values())
        story.section = story.lead.section if sections[story.lead.section] == top else sections.most_common(1)[0][0]
        story.india = any(a.region == "india" for a in story.articles) or bool(india_rx and india_rx.search(text))

        score = story.independent + fresh_weight * story.fresh + sensitive
        if not story.india and not matched:
            score *= global_multiplier
        story.score = round(score, 2)

        age_hours = max(0.0, (now - story.latest).total_seconds() / 3600)
        story.rank = story.score * 0.5 ** (age_hours / decay_hours)
        story.unconfirmed = bool(matched) and story.independent < 2
        story.hot = story.score >= hot_score and story.independent >= 2
