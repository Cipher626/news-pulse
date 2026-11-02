"""Offline end-to-end check. Run before pushing a change:

    python -m tests.smoke_test

Feeds are faked, nothing is sent, data/state.json is not touched.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from email.utils import format_datetime
from xml.sax.saxutils import escape

from pipeline import fetch, run
from pipeline.alerts import eligible, record_alert, select_alerts
from pipeline.cluster import ClusterSettings, cluster, tokenize
from pipeline.fetch import FeedStatus
from pipeline.score import score_stories
from pipeline.topics import TopicModel

NOW = dt.datetime.now(dt.timezone.utc)
QUAKE = "Earthquake of magnitude 6.1 strikes Nepal; tremors felt in Delhi, Bihar"
GN = "https://news.google.com/rss/articles/"

# Keyed by a unique substring of the feed URL in config.toml.
# Each item: (title, link, minutes_ago or None, google-news source name or None)
FAKE = {
    "thehindu.com/news/national": [
        (QUAKE, "https://www.thehindu.com/news/national/quake-nepal/article1.ece", 20, None),
        ("Cabinet approves semiconductor scheme worth Rs 76,000 crore", "https://www.thehindu.com/business/cabinet-semiconductor/article2.ece", 50, None),
    ],
    "hindustantimes.com/feeds/rss/india-news": [
        ("Strong earthquake jolts Nepal, tremors felt across Delhi-NCR", "https://www.hindustantimes.com/india-news/quake-101.html", 25, None),
    ],
    "ndtvnews-latest": [
        ("6.1 magnitude earthquake hits Nepal, tremors felt in Delhi", "https://www.ndtv.com/india-news/quake-nepal-123", 15, None),
    ],
    "indianexpress.com/section/india": [
        ("Nepal earthquake: 6.1 magnitude quake strikes, tremors in Delhi", "https://indianexpress.com/article/india/nepal-quake-1/", 30, None),
        ("Union Cabinet approves Rs 76,000-crore semiconductor scheme", "https://indianexpress.com/article/business/semicon-2/", 40, None),
    ],
    "rssfeedstopstories.cms": [  # both TOI and ET use this file name; fine for a fake
        (QUAKE, "https://timesofindia.indiatimes.com/india/quake/articleshow/1.cms", 22, None),  # wire copy
        ("Kohli hits century as India beat Australia in Perth", "https://timesofindia.indiatimes.com/sports/cricket/news/kohli-century/articleshow/2.cms", 60, None),
        ("Sensex crashes over 1,200 points, Nifty below 24,000 amid global selloff", "https://economictimes.indiatimes.com/markets/stocks/news/sensex-crash/articleshow/3.cms", 12, None),
    ],
    "indiatoday.in/rss/home": [
        ("Earthquake jolts Nepal, tremors felt in Delhi", "https://www.indiatoday.in/india/story/earthquake-nepal-delhi-tremors-2600001-2026-11-03", 28, None),
    ],
    "aajtak.in": [
        ("नेपाल में 6.1 तीव्रता का भूकंप, दिल्ली तक महसूस हुए झटके",
         "https://www.aajtak.in/india/news/story/nepal-earthquake-tremors-felt-in-delhi-ntc-2412345-2026-11-03", 19, None),
        ("भूकंप का वीडियो देखें", "https://www.aajtak.in/video/nepal-earthquake-video-2412346", 18, None),
    ],
    "indiatv.in/rssnews": [
        ("दिल्ली-एनसीआर में भूकंप के तेज झटके, नेपाल था केंद्र",
         "https://www.indiatv.in/india/national/delhi-ncr-earthquake-tremors-nepal-epicentre-2026-11-03-1100001", 17, None),
    ],
    "site:divyabhaskar.co.in": [
        ("ગુજરાતમાં ભારે વરસાદની આગાહી, અમદાવાદમાં યલો એલર્ટ - Divya Bhaskar", GN + "gu1", 35, "Divya Bhaskar"),
    ],
    "livemint.com/rss/news": [
        ("Sensex crashes 1,200 points as global selloff deepens", "https://www.livemint.com/market/stock-market-news/sensex-crash-111.html", 10, None),
    ],
    "site:moneycontrol.com": [
        ("Sensex, Nifty crash as global selloff deepens; investors lose Rs 6 lakh crore - Moneycontrol", GN + "mc1", 8, "Moneycontrol"),
    ],
    "business-standard.com/rss/latest": [
        ("Sensex crashes 1,200 points in global selloff", "https://www.business-standard.com/markets/news/sensex-crash-1.html", 9, None),
    ],
    "feeds.bbci.co.uk/news/world": [
        ("Nepal hit by powerful earthquake", "https://www.bbc.com/news/articles/nepal1", 18, None),
        ("Apple unveils new iPhone with satellite messaging", "https://www.bbc.com/news/articles/apple1", 300, None),
    ],
    "section/topic/TECHNOLOGY": [
        ("ISRO tests semiconductor-based navigation chip - The Hindu", GN + "isro1", 40, "The Hindu"),
    ],
    "pib.gov.in": [("Undated release about a scheme", "https://pib.gov.in/release/1", None, None)],
}


def rss(items):
    body = "".join(
        f"<item><title>{escape(t)}</title><link>{escape(l)}</link>"
        + (f"<pubDate>{format_datetime(NOW - dt.timedelta(minutes=m))}</pubDate>" if m is not None else "")
        + (f'<source url="https://x">{escape(src)}</source>' if src else "")
        + "</item>"
        for t, l, m, src in items
    )
    return f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


def fake_http_get(url, timeout=20):
    for key, items in FAKE.items():
        if key in url:
            return rss(items)
    raise fetch.requests.HTTPError(response=type("R", (), {"status_code": 403})())


def dead_http_get(url, timeout=20):
    raise fetch.requests.ConnectionError("offline")


def find(feed, needle):
    return next(s for s in feed["stories"] if needle in s["title"].lower())


def main():
    fetch.http_get = fake_http_get
    assert tokenize("Sensex crashes") == tokenize("sensex crashed"), "stemmer should merge crash forms"

    run.main(["--dry-run"])
    feed = json.loads((run.SITE_DIR / "feed.json").read_text())

    quake = find(feed, "earthquake of magnitude")
    names = [quake["outlet"]] + [c["outlet"] for c in quake["coverage"]]
    assert "Aaj Tak" in names and "India TV Hindi" in names, f"Hindi coverage should join via URL slugs: {names}"
    assert set(quake["langs"]) == {"en", "hi"}, quake["langs"]
    # 9 outlets; TOI is wire copy of The Hindu, Aaj Tak shares an owner with India Today
    assert quake["outlets"] == 9 and quake["independent"] == 7, (quake["outlets"], quake["independent"])
    assert quake["breaking"] and "Disaster" in quake["categories"]
    assert not any("/video/" in c["link"] for s in feed["stories"] for c in [s, *s["coverage"]]), "videos must be skipped"

    markets = find(feed, "sensex")
    assert "business" in markets["topics"] and markets["outlets"] >= 4, markets
    assert any(c["outlet"] == "Moneycontrol" for c in markets["coverage"]), "aggregator outlet name not parsed"

    kohli = find(feed, "kohli")
    assert kohli["topics"][:1] == ["sports"], kohli["topics"]

    rain = next(s for s in feed["stories"] if s["lang"] == "gu")
    assert {"gujarat", "environment"} & set(rain["topics"]), rain["topics"]

    isro = find(feed, "isro")
    assert isro["watched"] and "tech" in isro["topics"], isro

    iphone = find(feed, "iphone")
    assert iphone["india"] is False and iphone["outlets"] == 1
    assert {t["id"] for t in feed["topics"]} >= {"sports", "politics", "gujarat"}
    print(f"feed ok: {len(feed['stories'])} stories, top = {feed['stories'][0]['title']!r} ({feed['stories'][0]['score']})")

    # Alerts: memory, topic eligibility
    cfg = run.load_config()
    settings = ClusterSettings.from_config(cfg["cluster"])
    arts, _ = fetch.fetch_all(cfg["feeds"], NOW, skip=tuple(cfg["fetch"]["skip_url_patterns"]))
    for a in arts:
        a.published = a.published or NOW
    groups = cluster(arts, settings)
    TopicModel(cfg).assign(groups)
    score_stories(groups, cfg, settings, NOW)
    loud = {**cfg["alerts"], "quiet_start": "00:00", "quiet_end": "00:00"}
    state = {}
    first = select_alerts(groups, state, loud, settings, NOW, cfg["watch"])
    titles = [s.lead.title for s in first]
    assert any("Earthquake" in t for t in titles), titles
    for s in first:
        record_alert(state, s, NOW)
    assert not select_alerts(groups, state, loud, settings, NOW, cfg["watch"]), "repeat alert"
    sport = next(s for s in groups if "Kohli" in s.lead.title)
    sport.score, sport.independent = 20, 5
    assert not eligible(sport, cfg["alerts"]), "sports is not in alerts.topics, should not notify"
    print(f"alerts ok: {titles}")

    # Feed health: three failures in a row earns a 'failing since'
    state = {}
    for _ in range(3):
        status = FeedStatus("X", "https://x/feed", "india", False, error="HTTP 403")
        run.update_health(state, [status], NOW, 3)
    assert status.failing_since, "failing_since should be set after 3 failures"

    # Outage guard: nothing answers -> republish the previous feed, marked stale
    fetch.http_get = dead_http_get
    os.environ["SITE_URL"] = "https://example.github.io/news-pulse/"
    run.previous_feed = lambda url: dict(feed)
    run.main(["--dry-run"])
    stale = json.loads((run.SITE_DIR / "feed.json").read_text())
    assert stale["stale"] is True and len(stale["stories"]) == len(feed["stories"])
    assert all(not s["ok"] for s in stale["sources"])
    print("outage guard ok")
    print("smoke test passed")


if __name__ == "__main__":
    main()
