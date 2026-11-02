"""Download RSS/Atom feeds and turn their entries into Article objects.

Add an RSS source: add a [[feeds]] block in config.toml.
Add a non-RSS source (a news API, a search endpoint): write a function that
returns list[Article] and call it in run.py next to fetch_all().
"""
from __future__ import annotations

import calendar
import concurrent.futures as cf
import datetime as dt
from dataclasses import dataclass, field

import feedparser
import requests

from .util import clean_text

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 PulseNewsReader/1.0"
)
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"
MAX_ENTRIES_PER_FEED = 60
FUTURE_TOLERANCE = dt.timedelta(minutes=10)


@dataclass
class Article:
    title: str
    link: str
    outlet: str
    section: str                    # india | world | business
    region: str                     # india | world
    priority: int                   # lower wins when choosing a story's headline link
    published: dt.datetime | None
    first_seen: dt.datetime | None = None
    tokens: frozenset = field(default_factory=frozenset)


@dataclass
class FeedStatus:
    name: str
    section: str
    ok: bool
    count: int = 0
    error: str = ""


def http_get(url: str, timeout: int = 20) -> bytes:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT})
    response.raise_for_status()
    return response.content


def _entry_time(entry, now: dt.datetime) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            moment = dt.datetime.fromtimestamp(calendar.timegm(value), dt.timezone.utc)
            # Some feeds stamp IST times as UTC, which lands them in the future.
            # Fall back to the time we first saw the item instead of trusting it.
            return moment if moment <= now + FUTURE_TOLERANCE else None
    return None


def _split_aggregator_title(title: str, source_name: str) -> tuple[str, str]:
    """Google News appends ' - Outlet' to every headline; split it off."""
    if " - " not in title:
        return title, source_name
    head, tail = title.rsplit(" - ", 1)
    tail = tail.strip()
    if source_name and tail.lower() != source_name.lower():
        return title, source_name
    return head.strip(), source_name or tail


def _short_error(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.Timeout):
        return "timed out"
    if isinstance(exc, requests.ConnectionError):
        return "could not connect"
    return type(exc).__name__


def fetch_feed(feed: dict, priority: int, now: dt.datetime) -> tuple[list[Article], FeedStatus]:
    name = feed["name"]
    section = feed.get("section", "india")
    region = feed.get("region", "world" if section == "world" else "india")
    try:
        parsed = feedparser.parse(http_get(feed["url"]))
    except Exception as exc:  # one broken source must never stop the run
        return [], FeedStatus(name, section, False, error=_short_error(exc))

    entries = parsed.entries[:MAX_ENTRIES_PER_FEED]
    if not entries:
        reason = str(getattr(parsed, "bozo_exception", "") or "no items in feed")
        return [], FeedStatus(name, section, False, error=reason[:120])

    articles: list[Article] = []
    for entry in entries:
        title = clean_text(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        outlet = name
        if feed.get("aggregator"):
            source = entry.get("source") or {}
            source_name = clean_text(source.get("title")) if hasattr(source, "get") else ""
            title, outlet = _split_aggregator_title(title, source_name)
            outlet = outlet or name
        articles.append(
            Article(
                title=title,
                link=link,
                outlet=outlet,
                section=section,
                region=region,
                priority=priority,
                published=_entry_time(entry, now),
            )
        )
    return articles, FeedStatus(name, section, True, count=len(articles))


def fetch_all(feeds: list[dict], now: dt.datetime, workers: int = 8):
    results: list = [None] * len(feeds)
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_feed, feed, i, now): i for i, feed in enumerate(feeds)}
        for future in cf.as_completed(futures):
            results[futures[future]] = future.result()
    articles: list[Article] = []
    statuses: list[FeedStatus] = []
    for found, status in results:
        articles.extend(found)
        statuses.append(status)
    return articles, statuses
