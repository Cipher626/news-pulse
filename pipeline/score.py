"""How hot is each story? Every weight comes from config.toml."""
from __future__ import annotations

import datetime as dt
from collections import Counter

from .cluster import ClusterSettings, Story, jaccard
from .textmatch import matches, phrase_regex
from .util import outlet_key


class Categories:
    def __init__(self, entries: list[dict]):
        self.items = [
            (e["name"], e.get("label", e["name"].title()), float(e.get("weight", 1)), phrase_regex(e.get("keywords", [])))
            for e in entries
        ]

    def match(self, text: str) -> list[tuple[str, str, float]]:
        return [(name, label, weight) for name, label, weight, rx in self.items if matches(rx, text)]


def owner_map(cfg: dict) -> dict[str, str]:
    """outlet_key -> owner group, from [owners] in config.toml."""
    mapping = {}
    for group, outlets in cfg.get("owners", {}).items():
        for name in outlets:
            mapping[outlet_key(name)] = group
    return mapping


def independent_sources(story: Story, wire_similarity: float, owners: dict[str, str]) -> int:
    """Distinct newsrooms. Outlets with one owner (Aaj Tak and India Today) count once,
    and near-identical headlines from different outlets (wire copy) count once."""
    reps: list[frozenset] = []
    seen: set[str] = set()
    for art in sorted(story.articles, key=lambda a: a.published):
        key = outlet_key(art.outlet)
        key = owners.get(key, key)
        if key in seen:
            continue
        seen.add(key)
        if any(jaccard(art.tokens, rep) >= wire_similarity for rep in reps):
            continue
        reps.append(art.tokens)
    return len(reps)


def score_stories(stories: list[Story], cfg: dict, settings: ClusterSettings, now: dt.datetime, topic_weights: dict[str, float] | None = None) -> None:
    sc = cfg.get("scoring", {})
    fresh_minutes = sc.get("fresh_minutes", 90)
    fresh_weight = sc.get("fresh_weight", 0.5)
    global_multiplier = sc.get("global_multiplier", 0.7)
    decay_hours = sc.get("decay_hours", 6)
    hot_score = cfg.get("site", {}).get("hot_score", 6.0)
    breaking_weight = cfg.get("alerts", {}).get("breaking_weight", 3)

    categories = Categories(cfg.get("sensitive", []))
    india_rx = phrase_regex(cfg.get("india", {}).get("keywords", []))
    watch_rx = phrase_regex(cfg.get("watch", {}).get("keywords", []))
    owners = owner_map(cfg)
    topic_weights = topic_weights or {}
    fresh_cut = now - dt.timedelta(minutes=fresh_minutes)

    for story in stories:
        text = " | ".join(a.title for a in story.articles)
        matched = categories.match(text)
        story.category_names = [name for name, _, _ in matched]
        story.categories = [label for _, label, _ in matched]
        sensitive = max((w for _, _, w in matched), default=0.0)
        story.breaking = sensitive >= breaking_weight
        story.watched = matches(watch_rx, text)

        story.independent = independent_sources(story, settings.wire_similarity, owners)
        story.fresh = len({outlet_key(a.outlet) for a in story.articles if a.published >= fresh_cut})

        sections = Counter(a.section for a in story.articles)
        top = max(sections.values())
        story.section = story.lead.section if sections[story.lead.section] == top else sections.most_common(1)[0][0]
        story.india = story.section == "india" or matches(india_rx, text)

        score = story.independent + fresh_weight * story.fresh + sensitive
        if not story.india and not matched:
            score *= global_multiplier
        # Taste: topics you weight above 1 run hotter, below 1 cooler. Breaking news is never muted.
        if story.topics and not story.breaking:
            score *= max(topic_weights.get(t, 1.0) for t in story.topics)
        story.score = round(score, 2)

        age_hours = max(0.0, (now - story.latest).total_seconds() / 3600)
        story.rank = story.score * 0.5 ** (age_hours / decay_hours)
        story.unconfirmed = story.breaking and story.independent < 2
        story.hot = story.score >= hot_score and story.independent >= 2
