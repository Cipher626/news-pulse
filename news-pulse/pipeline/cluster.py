"""Group articles about the same event into stories.

Headline word overlap, in English, Hindi and Gujarati. Hindi and Gujarati sites
usually put English words in their URLs, so those words are added to the
article's tokens: that is what lets an Aaj Tak story join the English coverage of
the same event. If an outlet uses transliterated URLs instead, its stories simply
stay separate.

This is the module to upgrade first (multilingual embeddings). Keep the interface,
cluster(articles, settings) -> list[Story], and nothing else has to change.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .fetch import Article
from .util import DEVANAGARI, GUJARATI, normalize, outlet_key, url_parts

_TOKEN = re.compile(rf"[a-z0-9]+|[\u0900-\u0963\u0966-\u097f]+|[{GUJARATI}]+")
_INDIC = re.compile(rf"[{DEVANAGARI}{GUJARATI}]")
_FILE_EXT = re.compile(r"\.(html?|cms|php|aspx?)$")

STOPWORDS_EN = """
a about above after again against all also am amid an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from further
had has have having he her here hers him his how if in into is it its itself just me more most my
no nor not now of off on once only or other our out over own same say says said she should so some
such than that the their theirs them then there these they this those through to too under until
up very was we were what when where which while who whom why will with would you your yours
news live latest update updates today tonight breaking watch video videos photos pics report
reports know check details explained heres top big ahead day days week weeks year years first
last next one two three per via india indian rs crore lakh percent cent 000 2025 2026 2027
get gets got set sets make makes made take takes took amp html htm cms php aspx story article
"""
STOPWORDS_HI = """
का की के को में से पर ने है हैं था थी थे और या भी तो ही एक यह वह ये वो इस उस इन उन लिए लिये
साथ बाद पहले अब तक कर करें करने किया किए गई गया गए गयी हुआ हुई हुए हो होगा होगी होंगे रहा
रही रहे जा जाएगा जाएगी नहीं बड़ा बड़ी बड़े ताजा खबर खबरें लाइव अपडेट वीडियो आज कहा बोले
बोला बोली अपने अपनी अपना सकते सकता सकती दिया दी दिए लेकर बीच बारे कैसे क्या क्यों कौन जब
जो जिस जिसे सबसे पूरी पूरा न्यूज़ समाचार
"""
STOPWORDS_GU = """
નો ની નું ના ને માં થી પર છે હતો હતી હતું હતા અને કે પણ જ એક આ તે માટે સાથે બાદ પછી હવે સુધી
કર્યો કરી કર્યું કરવા કરશે થયો થઈ થયું થયા ગયો ગઈ ગયું ગયા રહ્યો રહી રહ્યું રહ્યા નથી શકે
આજે સમાચાર લાઈવ અપડેટ વીડિયો કહ્યું તેમ જે
"""
STOPWORDS = frozenset(normalize(w) for w in (STOPWORDS_EN + STOPWORDS_HI + STOPWORDS_GU).split())


def stem(word: str) -> str:
    """Crude English suffix stripping. Consistency matters more than accuracy:
    'crashes'/'crashed'/'crash' and 'rise'/'rises'/'rising' must land on one token."""
    if word.isdigit() or len(word) <= 3 or not word.isascii():
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


def tokenize(text: str) -> frozenset:
    out = set()
    for word in _TOKEN.findall(normalize(text)):
        if word in STOPWORDS:
            continue
        if word.isascii():
            if word.isdigit():
                if len(word) < 2:
                    continue
            elif len(word) < 3:
                continue
        elif len(word) < 2:
            continue
        token = stem(word)
        if token not in STOPWORDS:
            out.add(token)
    return frozenset(out)


def slug_tokens(url: str) -> frozenset:
    """English words from a URL slug, e.g. /india/story/delhi-earthquake-tremors-1234 ->
    {delhi, earthquak, tremor}. IDs, dates and codes like 'ntc12' are dropped."""
    host, segments = url_parts(url)
    if "news.google." in host or not segments:
        return frozenset()
    slug = max(segments, key=lambda s: s.count("-"))
    if slug.count("-") < 2 or _INDIC.search(slug):
        return frozenset()
    words = _FILE_EXT.sub("", slug).replace("-", " ").replace("_", " ")
    return frozenset(t for t in tokenize(words) if t.isalpha())


def article_tokens(art: Article) -> frozenset:
    tokens = tokenize(art.title)
    if art.lang != "en":
        tokens |= slug_tokens(art.link)
    return tokens


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
    # Filled in by score.py / topics.py
    score: float = 0.0
    rank: float = 0.0
    independent: int = 0
    fresh: int = 0
    category_names: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    india: bool = False
    section: str = "india"
    hot: bool = False
    breaking: bool = False
    watched: bool = False
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

    @property
    def langs(self) -> list[str]:
        return sorted({a.lang for a in self.articles})

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
    by_title: set[tuple[str, str]] = set()
    for art in sorted(articles, key=lambda a: a.priority):
        title_key = (outlet_key(art.outlet), normalize(art.title))
        if art.link in by_link or title_key in by_title:
            continue
        by_link[art.link] = art
        by_title.add(title_key)
    return list(by_link.values())


def cluster(articles: list[Article], settings: ClusterSettings) -> list[Story]:
    """Single-linkage grouping: two articles end up in the same story if a chain of
    similar headlines connects them. Order-independent (union-find)."""
    items = sorted(_dedupe(articles), key=lambda a: a.published)
    for art in items:
        art.tokens = article_tokens(art)

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
