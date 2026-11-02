"""Which stories become phone notifications. Thresholds live in [alerts] in config.toml.

This file is the policy. notify.py is the delivery channel. Change one without the other.
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


def select_alerts(stories: list[Story], state: dict, cfg: dict, settings: ClusterSettings, now: dt.datetime) -> list[Story]:
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
        if story.score < cfg.get("score", 8.0) or story.independent < cfg.get("min_outlets", 3):
            continue
        if quiet and not story.categories:
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
