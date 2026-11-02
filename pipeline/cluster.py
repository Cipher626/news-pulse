"""Group articles about the same event into stories.

Deliberately simple: headline word overlap. This is the first module to upgrade
(sentence embeddings or an LLM pass) once the rest is stable. Keep the interface,
cluster(articles, settings) -> list[Story], and nothing else has to change.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .fetch import Article
from .util import outlet_key

_WORD = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a about above after again against all also am amid an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from further
    had has have having he her here hers him his how if in into is it its itself just me more most my
    no nor not now of off on once only or other our out over own same say says said she should so some
    such than that the their theirs them then there these they this those through to too under until
    up very was we were what when where which while who whom why will with would you your yours
    news live latest update updates today tonight breaking watch video videos photos pics report
    reports know check details explained heres top big ahead day days week weeks year years first
    last next one two three per via india indian rs crore lakh percent cent 000 2025 2026 2027
    get gets got set sets make makes made take takes took amp
    """.split()
)


def stem(word: str) -> str:
    """Crude suffix stripping. Consistency matters more than linguistic accuracy:
    'crashes'/'crashed'/'crash' and 'rise'/'rises'/'rising' must land on the same token."""
    if word.isdigit() or len(word) <= 3:
        return word
    base = word
    for suffix in ("ings", "ing", "ies", "ied", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            base = word[: -len(suffix)]
            if suffix in ("ies", "ied"):
                base += "y"
            elif suffix == "es" and not base.endswith(("sh", "ch", "x", "ss", "z")):
                base = word[:-1]
            elif suffix == "s" and word.endswith("ss"):
                base = word
            break
    if len(base) > 3 and base[-1] == base[-2] and base[-1] not in "ls":
        base = base[:-1]
    if len(base) > 3 and base.endswith("e"):
        base = base[:-1]
    return base


def tokenize(title: str) -> frozenset:
    out = set()
    for word in _WORD.findall(title.lower()):
        if word in STOPWORDS:
            continue
        if word.isdigit():
            if len(word) < 2:
                continue
        elif len(word) < 3:
            continue
        token = stem(word)
        if token not in STOPWORDS:
            out.add(token)
    return frozenset(out)


@dataclass
class ClusterSettings:
    min_shared: int = 3
    min_overlap: float = 0.45
    strong_shared: int = 2
    strong_overlap: float = 0.66
    wire_similarity: float = 0.9

    @classmethod
    def from_config(cls, cfg: dict) -> "ClusterSettings":
        return cls(**{k: v for k, v in cfg.items() if k in cls.__dataclass_fields__})


def similar(a: frozenset, b: frozenset, s: ClusterSettings) -> bool:
    if not a or not b:
        return False
    shared = len(a & b)
    if shared < min(s.min_shared, s.strong_shared):
        return False
    overlap = shared / min(len(a), len(b))
    return (shared >= s.min_shared and overlap >= s.min_overlap) or (
        shared >= s.strong_shared and overlap >= s.strong_overlap
    )


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass
class Story:
    articles: list[Article] = field(default_factory=list)
    # Filled in by score.py
    score: float = 0.0
    rank: float = 0.0
    independent: int = 0
    fresh: int = 0
    category_names: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    india: bool = False
    section: str = "india"
    hot: bool = False
    unconfirmed: bool = False
    alerted: bool = False

    @property
    def lead(self) -> Article:
        return min(self.articles, key=lambda a: (a.priority, a.published))

    @property
    def latest(self):
        return max(a.published for a in self.articles)

    @property
    def first_seen(self):
        return min((a.first_seen or a.published) for a in self.articles)

    @property
    def tokens(self) -> frozenset:
        return frozenset().union(*(a.tokens for a in self.articles))

    def core_tokens(self, limit: int = 40) -> list[str]:
        """Most frequent tokens across the story's headlines, for remembering alerts."""
        counts = Counter(t for a in self.articles for t in a.tokens)
        return [t for t, _ in counts.most_common(limit)]

    def outlet_names(self) -> list[str]:
        names, seen = [], set()
        for a in sorted(self.articles, key=lambda a: (a.priority, a.published)):
            key = outlet_key(a.outlet)
            if key not in seen:
                seen.add(key)
                names.append(a.outlet)
        return names


def _dedupe(articles: list[Article]) -> list[Article]:
    """Same link in two feeds, or the same headline twice from one outlet, counts once."""
    by_link: dict[str, Article] = {}
    by_title: dict[tuple[str, str], Article] = {}
    for art in sorted(articles, key=lambda a: a.priority):
        title_key = (outlet_key(art.outlet), art.title.lower())
        if art.link in by_link or title_key in by_title:
            continue
        by_link[art.link] = art
        by_title[title_key] = art
    return list(by_link.values())


def cluster(articles: list[Article], settings: ClusterSettings) -> list[Story]:
    """Single-linkage grouping: two articles end up in the same story if a chain of
    similar headlines connects them. Order-independent (union-find)."""
    for art in articles:
        art.tokens = tokenize(art.title)
    items = sorted(_dedupe(articles), key=lambda a: a.published)

    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index: dict[str, list[int]] = {}
    for i, art in enumerate(items):
        candidates = {j for token in art.tokens for j in index.get(token, ())}
        for j in candidates:
            if find(i) != find(j) and similar(art.tokens, items[j].tokens, settings):
                parent[find(i)] = find(j)
        for token in art.tokens:
            index.setdefault(token, []).append(i)

    groups: dict[int, list[Article]] = {}
    for i, art in enumerate(items):
        groups.setdefault(find(i), []).append(art)
    return [Story(articles=members) for members in groups.values()]
