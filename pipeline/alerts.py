"""Which stories become phone notifications. Settings live in [alerts] and [watch] in config.toml.

This file is the policy. notify.py is the delivery channel. Change one without the other.

A story notifies when all of these hold:
  - it clears the bar: score >= alerts.score and independent outlets >= alerts.min_outlets
    (a topic's alert_score / alert_min_outlets, or the watch list's bar, can lower it)
  - it is eligible: breaking news, a watched keyword, or a topic in alerts.topics
  - it is not quiet hours, unless it is breaking
  - it was not already notified inside cooldown_hours, and the per-run/per-day caps allow it
"""
from __future__ import annotations

import datetime as dt

from .cluster import ClusterSettings, Story, similar
from .util import IST, from_iso, to_iso


def in_quiet_hours(now: dt.datetime, start: str, end: str) -> bool:
    local = now.astimezone(IST).time()
    begin, finish = dt.time.fromisoformat(start), dt.time.fromisoformat(end)
    if begin <= finish:
        return begin <= local < finish
    return local >= begin or local < finish


def _today(state: dict, now: dt.datetime) -> dict:
    date = now.astimezone(IST).date().isoformat()
    day = state.get("alert_day") or {}
    if day.get("date") != date:
        day = {"date": date, "count": 0}
    state["alert_day"] = day
    return day


def recent_alerts(state: dict, cfg: dict, now: dt.datetime) -> list[dict]:
    cutoff = now - dt.timedelta(hours=cfg.get("cooldown_hours", 24))
    return [a for a in state.get("alerted", []) if from_iso(a["at"]) >= cutoff]


def eligible(story: Story, cfg: dict) -> bool:
    if story.breaking or story.watched:
        return True
    allowed = cfg.get("topics")
    if allowed is None:
        return True
    if not story.topics:
        return bool(cfg.get("alert_untagged", True))
    return bool(set(story.topics) & set(allowed))


def alert_bar(story: Story, cfg: dict, watch: dict, topic_bars: dict | None = None) -> tuple[float, int]:
    """The (score, outlets) this story must reach to notify: the lowest bar it qualifies for."""
    score, outlets = cfg.get("score", 8.0), cfg.get("min_outlets", 3)
    for tid in story.topics:
        t_score, t_outlets = (topic_bars or {}).get(tid, (None, None))
        if t_score is not None and t_score < score:
            score = t_score
        if t_outlets is not None and t_outlets < outlets:
            outlets = t_outlets
    if story.watched:
        score, outlets = min(score, watch.get("alert_score", 4.0)), min(outlets, watch.get("min_outlets", 2))
    return score, outlets


def clears_bar(story: Story, cfg: dict, watch: dict, topic_bars: dict | None = None) -> bool:
    score, outlets = alert_bar(story, cfg, watch, topic_bars)
    return story.score >= score and story.independent >= outlets


def select_alerts(
    stories: list[Story], state: dict, cfg: dict, settings: ClusterSettings, now: dt.datetime,
    watch: dict | None = None, topic_bars: dict | None = None,
) -> list[Story]:
    watch = watch or {}
    history = [frozenset(a["tokens"]) for a in recent_alerts(state, cfg, now)]
    day = _today(state, now)
    budget = max(0, min(cfg.get("max_per_run", 3), cfg.get("max_per_day", 15) - day["count"]))
    quiet = in_quiet_hours(now, cfg.get("quiet_start", "23:30"), cfg.get("quiet_end", "07:00"))

    picks: list[Story] = []
    for story in sorted(stories, key=lambda s: s.score, reverse=True):
        tokens = story.tokens
        if any(similar(tokens, past, settings) for past in history):
            story.alerted = True
            continue
        if not cfg.get("enabled", True) or len(picks) >= budget:
            continue
        if not clears_bar(story, cfg, watch, topic_bars) or not eligible(story, cfg):
            continue
        if quiet and not story.breaking:
            continue
        if any(similar(tokens, p.tokens, settings) for p in picks):
            continue
        picks.append(story)
    return picks


def record_alert(state: dict, story: Story, now: dt.datetime) -> None:
    state.setdefault("alerted", []).append(
        {"at": to_iso(now), "title": story.lead.title, "tokens": story.core_tokens()}
    )
    _today(state, now)["count"] += 1
    story.alerted = True
