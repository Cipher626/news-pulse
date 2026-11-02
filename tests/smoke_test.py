"""Offline end-to-end check. Run before pushing a change:

    python -m tests.smoke_test

Feeds are faked, nothing is sent, data/state.json is not touched.
"""
from __future__ import annotations

import datetime as dt
import json
from email.utils import format_datetime

from pipeline import fetch, run
from pipeline.alerts import record_alert, select_alerts
from pipeline.cluster import ClusterSettings, cluster, tokenize
from pipeline.score import score_stories

NOW = dt.datetime.now(dt.timezone.utc)


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link>"
        + (f"<pubDate>{format_datetime(NOW - dt.timedelta(minutes=m))}</pubDate>" if m is not None else "")
        + (f'<source url="https://x">{src}</source>' if src else "")
        + "</item>"
        for t, l, m, src in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


QUAKE = "Earthquake of magnitude 6.1 strikes Nepal; tremors felt in Delhi, Bihar"
FAKE = {
    "The Hindu": [(QUAKE, "https://hindu.example/q", 20, None),
                  ("Cabinet approves semiconductor scheme worth Rs 76,000 crore", "https://hindu.example/c", 50, None)],
    "Hindustan Times": [("Strong earthquake jolts Nepal, tremors felt across Delhi-NCR", "https://ht.example/q", 25, None)],
    "NDTV": [("6.1 magnitude earthquake hits Nepal, tremors felt in Delhi", "https://ndtv.example/q", 15, None)],
    "Indian Express": [("Nepal earthquake: 6.1 magnitude quake strikes, tremors in Delhi", "https://ie.example/q", 30, None),
                       ("Union Cabinet approves Rs 76,000-crore semiconductor scheme", "https://ie.example/c", 40, None)],
    "Times of India": [(QUAKE, "https://toi.example/q", 22, None),  # wire copy of The Hindu
                       ("Cabinet nod for semiconductor scheme worth Rs 76,000 crore", "https://toi.example/c", 45, None)],
    "Mint": [("Sensex crashes 1,200 points as global selloff deepens", "https://mint.example/s", 10, None)],
    "Economic Times": [("Sensex crashes over 1,200 points, Nifty below 24,000 amid global selloff", "https://et.example/s", 12, None)],
    "Moneycontrol": [("Sensex, Nifty crash as global selloff deepens; investors lose Rs 6 lakh crore", "https://mc.example/s", 8, None)],
    "BBC News": [("Nepal hit by powerful earthquake", "https://bbc.example/q", 18, None),
                 ("Apple unveils new iPhone with satellite messaging", "https://bbc.example/a", 300, None)],
    "PIB": [("Undated release about a scheme", "https://pib.example/u", None, None)],
    "Google News Business": [("Sensex crashes 1,200 points in global selloff - Business Standard", "https://news.google.example/s", 9, "Business Standard")],
}


def fake_http_get(url, timeout=20):
    for feed in run.load_config()["feeds"]:
        if feed["url"] == url and feed["name"] in FAKE:
            return rss(FAKE[feed["name"]])
    raise fetch.requests.HTTPError(response=type("R", (), {"status_code": 403})())


def main():
    fetch.http_get = fake_http_get
    assert tokenize("Sensex crashes") == tokenize("sensex crashed"), "stemmer should merge crash forms"

    run.main(["--dry-run"])
    feed = json.loads((run.SITE_DIR / "feed.json").read_text())
    stories = {s["title"]: s for s in feed["stories"]}

    quake = next(s for s in feed["stories"] if "earthquake" in s["title"].lower())
    assert quake["outlets"] >= 5, quake
    assert quake["independent"] == quake["outlets"] - 1, "TOI wire copy should not count as independent"
    assert "Disaster" in quake["categories"], quake
    assert quake["score"] >= feed["thresholds"]["alert"], quake

    markets = next(s for s in feed["stories"] if "sensex" in s["title"].lower())
    assert markets["outlets"] >= 4 and "Markets" in markets["categories"], markets
    assert any(c["outlet"] == "Business Standard" for c in markets["coverage"]), "aggregator outlet name not parsed"

    iphone = next(s for s in feed["stories"] if "iphone" in s["title"].lower())
    assert iphone["india"] is False and iphone["outlets"] == 1

    assert any(not s["ok"] and s["error"] == "HTTP 403" for s in feed["sources"])
    print(f"feed ok: {len(stories)} stories, top = {feed['stories'][0]['title']!r} ({feed['stories'][0]['score']})")

    # Alert memory: a story already notified must not notify again.
    cfg = run.load_config()
    settings = ClusterSettings.from_config(cfg["cluster"])
    arts, _ = fetch.fetch_all(cfg["feeds"], NOW)
    for a in arts:
        a.published = a.published or NOW
    groups = cluster(arts, settings)
    score_stories(groups, cfg, settings, NOW)
    state = {}
    first = select_alerts(groups, state, {**cfg["alerts"], "quiet_start": "00:00", "quiet_end": "00:00"}, settings, NOW)
    assert first, "expected at least one alert"
    for s in first:
        record_alert(state, s, NOW)
    again = select_alerts(groups, state, {**cfg["alerts"], "quiet_start": "00:00", "quiet_end": "00:00"}, settings, NOW)
    assert not again, [s.lead.title for s in again]
    print(f"alerts ok: {[s.lead.title for s in first]}")
    print("smoke test passed")


if __name__ == "__main__":
    main()
