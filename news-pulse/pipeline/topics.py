"""What is each story about? Topics are defined in config.toml ([[topics]]).

An article votes for a topic from four signals, strongest first:
  3  the feed itself is about that topic (topic = "sports" on a [[feeds]] block)
  2  a URL segment names it (/sports/, /cricket/, sports.ndtv.com, /local/gujarat/)
  2  an RSS <category> names it
  1  a headline keyword matches
A story gets the topics whose summed votes reach 2, at most two, strongest first.
If none reach 2 (a lone headline from a Google News link, say), keyword votes are enough.
URL segments and categories work the same in Hindi and Gujarati feeds, which is
why non-English stories still get topics without translated keyword lists.
"""
from __future__ import annotations

from dataclasses import dataclass

from .cluster import Story
from .fetch import Article
from .textmatch import matches, phrase_regex
from .util import normalize, url_parts


@dataclass
class Topic:
    id: str
    label: str
    default: bool
    paths: frozenset
    keywords: object  # compiled regex or None


class TopicModel:
    def __init__(self, cfg: dict):
        self.topics = [
            Topic(
                id=t["id"],
                label=t.get("label", t["id"].title()),
                default=bool(t.get("default", True)),
                paths=frozenset(p.lower() for p in t.get("paths", [])),
                keywords=phrase_regex(t.get("keywords", [])),
            )
            for t in cfg.get("topics", [])
        ]
        self.by_id = {t.id: t for t in self.topics}
        self.world_paths = frozenset(p.lower() for p in cfg.get("sections", {}).get("world_paths", []))

    def refine_section(self, art: Article) -> None:
        """A general Indian feed still carries world news; its URL usually says so."""
        if art.section != "world" and self.world_paths:
            _, segments = url_parts(art.link)
            if any(s in self.world_paths for s in segments[:-1]):
                art.section = "world"

    def article_weights(self, art: Article) -> dict[str, float]:
        host, segments = url_parts(art.link)
        if "news.google." in host:
            segments = []
        url_words = set(segments[:-1]) | set(host.split(".")[:-2])
        categories = {normalize(c) for c in art.categories}
        weights: dict[str, float] = {}
        for topic in self.topics:
            w = 0.0
            if art.feed_topic == topic.id:
                w += 3
            if url_words & topic.paths:
                w += 2
            if categories & topic.paths or any(matches(topic.keywords, c) for c in categories):
                w += 2
            if matches(topic.keywords, art.title):
                w += 1
            if w:
                weights[topic.id] = w
        return weights

    def assign(self, stories: list[Story]) -> None:
        for story in stories:
            totals: dict[str, float] = {}
            for art in story.articles:
                art.topic_weights = self.article_weights(art)
                for tid, w in art.topic_weights.items():
                    totals[tid] = totals.get(tid, 0.0) + w
            ranked = sorted(totals.items(), key=lambda t: t[1], reverse=True)
            strong = [tid for tid, w in ranked if w >= 2]
            story.topics = (strong or [tid for tid, _ in ranked])[:2]

    def as_json(self) -> list[dict]:
        return [{"id": t.id, "label": t.label, "default": t.default} for t in self.topics]
