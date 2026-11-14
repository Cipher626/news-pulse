"""Entry point.

    python -m pipeline.run             # what GitHub Actions runs every 30 minutes
    python -m pipeline.run --dry-run   # local check: builds site/, prints alerts, sends nothing, saves no state

Steps: fetch -> cluster -> topics -> score -> pick alerts -> notify -> write site/feed.json -> save state.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

import requests

from .alerts import alert_bar, eligible, record_alert, recent_alerts, select_alerts
from .cluster import ClusterSettings, Story, cluster
from .fetch import FeedStatus, fetch_all
from .notify import send_story, send_test
from .schedule import next_runs, read_cron
from .score import score_stories
from .topics import TopicModel
from .util import from_iso, now_utc, outlet_key, to_iso

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
STATE_PATH = ROOT / "data" / "state.json"
WEB_DIR = ROOT / "web"
SITE_DIR = ROOT / "site"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "news.yml"

LANGUAGES = [{"id": "en", "label": "English"}, {"id": "hi", "label": "हिन्दी"}, {"id": "gu", "label": "ગુજરાતી"}]


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def site_url() -> str:
    explicit = os.environ.get("SITE_URL", "").strip()
    if explicit:
        return explicit if explicit.endswith("/") else explicit + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"


def update_health(state: dict, statuses: list[FeedStatus], now: dt.datetime, failing_after: int) -> None:
    """Remember how long each feed has been failing, so the footer can tell a blip from a dead feed."""
    health = state.get("health", {})
    current = {}
    for s in statuses:
        entry = health.get(s.url, {})
        if s.ok:
            entry = {"fails": 0, "last_ok": to_iso(now)}
        else:
            entry = {**entry, "fails": entry.get("fails", 0) + 1}
            entry.setdefault("since", to_iso(now))
            if entry["fails"] >= failing_after:
                s.failing_since = entry["since"]
        current[s.url] = entry
    state["health"] = current  # feeds removed from config drop out


def _key(link: str) -> str:
    return hashlib.sha1(link.encode()).hexdigest()[:12]


def schedule_info(now: dt.datetime, fallback_minutes: int) -> dict:
    """When the next run is due, read from the workflow's cron so the page never guesses."""
    cron = read_cron(WORKFLOW_PATH)
    runs = next_runs(cron, now, 2) if cron else []
    if len(runs) == 2:
        return {"schedule": cron, "next_run_at": to_iso(runs[0]), "interval_minutes": int((runs[1] - runs[0]).total_seconds() // 60)}
    return {"schedule": cron, "next_run_at": None, "interval_minutes": fallback_minutes}


def story_json(story: Story, alert_cfg: dict, watch: dict, topic_bars: dict) -> dict:
    lead = story.lead
    coverage, seen = [], {outlet_key(lead.outlet)}
    for art in sorted(story.articles, key=lambda a: (a.priority, a.published)):
        key = outlet_key(art.outlet)
        if key in seen:
            continue
        seen.add(key)
        coverage.append({"title": art.title, "link": art.link, "outlet": art.outlet, "lang": art.lang, "published": to_iso(art.published)})
    bar_score, bar_outlets = alert_bar(story, alert_cfg, watch, topic_bars)
    return {
        "id": _key(lead.link),
        # One key per article: the page remembers which it has shown you, whatever the story is called later
        "keys": [_key(lead.link)] + [_key(c["link"]) for c in coverage[:12]],
        "needs": {"score": bar_score, "outlets": bar_outlets, "eligible": eligible(story, alert_cfg)},
        "title": lead.title,
        "link": lead.link,
        "outlet": lead.outlet,
        "lang": lead.lang,
        "langs": story.langs,
        "section": story.section,
        "topics": story.topics,
        "india": story.india,
        "score": story.score,
        "rank": round(story.rank, 3),
        "hot": story.hot,
        "breaking": story.breaking,
        "watched": story.watched,
        "alerted": story.alerted,
        "outlets": len(seen),
        "independent": story.independent,
        "categories": story.categories,
        "unconfirmed": story.unconfirmed,
        "latest": to_iso(story.latest),
        "first_seen": to_iso(story.first_seen),
        "coverage": coverage[:12],
    }


def sources_json(statuses: list[FeedStatus]) -> list[dict]:
    return [
        {"name": s.name, "section": s.section, "ok": s.ok, "count": s.count, "error": s.error, "failing_since": s.failing_since}
        for s in statuses
    ]


def write_site(payload: dict) -> Path:
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    shutil.copytree(WEB_DIR, SITE_DIR)
    out = SITE_DIR / "feed.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return out


def previous_feed(url: str) -> dict | None:
    """The feed currently live on the site, used when this run could not reach enough sources."""
    if not url:
        return None
    try:
        response = requests.get(url + "feed.json", timeout=15)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) and "stories" in data else None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Pulse news feed.")
    parser.add_argument("--dry-run", action="store_true", help="send nothing and leave data/state.json untouched")
    args = parser.parse_args(argv)

    cfg = load_config()
    state = load_state()
    now = now_utc()
    site_cfg = cfg.get("site", {})
    fetch_cfg = cfg.get("fetch", {})
    alert_cfg = cfg.get("alerts", {})
    settings = ClusterSettings.from_config(cfg.get("cluster", {}))
    topics = TopicModel(cfg)
    topic_bars = topics.alert_bars()
    watch_cfg = cfg.get("watch", {})
    url = site_url()

    # 1. Fetch
    feeds = cfg.get("feeds", [])
    skip = tuple(p.lower() for p in fetch_cfg.get("skip_url_patterns", []))
    articles, statuses = fetch_all(feeds, now, skip=skip)
    ignored = {outlet_key(name) for name in fetch_cfg.get("ignore_outlets", [])}
    if ignored:
        articles = [a for a in articles if outlet_key(a.outlet) not in ignored]
    ok = sum(s.ok for s in statuses)
    update_health(state, statuses, now, fetch_cfg.get("failing_after_runs", 3))
    print(f"Fetched {len(articles)} items from {ok}/{len(statuses)} feeds")
    for status in statuses:
        if not status.ok:
            since = f" (failing since {status.failing_since[:10]})" if status.failing_since else ""
            print(f"  skipped {status.name}: {status.error}{since}")

    meta = {
        "title": site_cfg.get("title", "Pulse"),
        **schedule_info(now, site_cfg.get("interval_minutes", 30)),
        "thresholds": {"hot": site_cfg.get("hot_score", 6.0), "alert": alert_cfg.get("score", 8.0), "min_outlets": alert_cfg.get("min_outlets", 3)},
        "tabs": site_cfg.get("tabs", ["foryou", "top", "india", "world"]),
        "alert_topics": alert_cfg.get("topics"),
        "topics": topics.as_json(),
        "languages": LANGUAGES,
    }

    # 1b. Outage guard: too few sources answered, keep the last good page instead of a thin one
    min_ok = fetch_cfg.get("min_sources_ok", 5)
    if ok < min_ok:
        previous = previous_feed(url)
        if previous:
            previous.update(meta)
            previous.update({"stale": True, "checked_at": to_iso(now), "sources": sources_json(statuses)})
            write_site(previous)
            print(f"Only {ok} sources responded (minimum {min_ok}); republished the previous feed, no alerts")
            if not args.dry_run:
                save_state(state)
            return 0
        print(f"Only {ok} sources responded and no previous feed was found; publishing what we have")

    # 2. Timestamps: items without a usable date get the time we first saw them
    seen: dict[str, str] = state.get("seen", {})
    for art in articles:
        first = seen.get(art.link)
        if first is None:
            seen[art.link] = to_iso(now)
            art.first_seen = now
        else:
            art.first_seen = from_iso(first)
        if art.published is None:
            art.published = art.first_seen
        topics.refine_section(art)
    cutoff = now - dt.timedelta(hours=site_cfg.get("max_age_hours", 24))
    articles = [a for a in articles if a.published >= cutoff]

    # 3. Cluster, label topics, score
    stories = cluster(articles, settings)
    topics.assign(stories)
    score_stories(stories, cfg, settings, now, topics.weights())
    print(f"Grouped into {len(stories)} stories, {sum(s.hot for s in stories)} hot")

    # 4. Alerts
    picks = select_alerts(stories, state, alert_cfg, settings, now, watch_cfg, topic_bars)
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    server = os.environ.get("NTFY_SERVER", "").strip() or "https://ntfy.sh"

    if os.environ.get("TEST_ALERT", "").strip().lower() == "true":
        if topic and not args.dry_run:
            top = max(stories, key=lambda s: s.rank, default=None)
            try:
                send_test(server=server, topic=topic, site_url=url, top=top)
                print("Sent test notification")
            except Exception as exc:
                print(f"Test notification failed: {exc}", file=sys.stderr)
        else:
            print("Test notification skipped: NTFY_TOPIC not set or dry run")

    if not state.get("initialized") and not args.dry_run:
        # First run: everything already big is old news to you. Remember it, don't ping.
        for story in picks:
            record_alert(state, story, now)
        state["alert_day"]["count"] = 0
        state["initialized"] = True
        print(f"First run: marked {len(picks)} existing big stories as seen without notifying")
        picks = []

    if os.environ.get("SKIP_ALERTS", "").strip().lower() == "true" and picks:
        # A run started by your own push rebuilds the page but leaves notifying to the next scheduled run,
        # so editing config never causes a surprise burst of pings.
        print(f"Push-triggered run: {len(picks)} alert(s) deferred to the next scheduled run")
        picks = []

    for story in picks:
        label = f"[{story.score}] {story.lead.title}"
        if args.dry_run:
            print(f"would notify: {label}")
            continue
        if not topic:
            print(f"NTFY_TOPIC not set, not sending: {label}")
            continue
        try:
            send_story(story, server=server, topic=topic, site_url=url)
            record_alert(state, story, now)
            print(f"notified: {label}")
        except Exception as exc:
            print(f"Notification failed for {label}: {exc}", file=sys.stderr)

    # 5. Publish
    stories.sort(key=lambda s: s.rank, reverse=True)
    payload = {
        **meta,
        "generated_at": to_iso(now),
        "stale": False,
        "sources": sources_json(statuses),
        "stories": [story_json(s, alert_cfg, watch_cfg, topic_bars) for s in stories[: site_cfg.get("max_stories", 200)]],
    }
    out = write_site(payload)
    print(f"Wrote {out.relative_to(ROOT)}")

    # 6. Remember what we saw (pruned so the file stays small)
    if not args.dry_run:
        keep_after = now - dt.timedelta(hours=site_cfg.get("max_age_hours", 24) + 24)
        state["seen"] = {link: t for link, t in seen.items() if from_iso(t) >= keep_after}
        state["alerted"] = recent_alerts(state, {"cooldown_hours": 2 * alert_cfg.get("cooldown_hours", 24)}, now)
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
