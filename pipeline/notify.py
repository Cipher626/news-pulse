"""Push notifications through ntfy (https://ntfy.sh): free, no account, Android and iOS apps.

To switch channel (Telegram, email, web push), replace send_story() and send_test().
Nothing else in the pipeline knows which channel is used.
"""
from __future__ import annotations

import requests

from .cluster import Story

# ntfy turns these tag names into emoji in the notification.
CATEGORY_TAGS = {
    "violence": "rotating_light",
    "disaster": "warning",
    "conflict": "crossed_swords",
    "markets": "chart_with_downwards_trend",
    "politics": "classical_building",
    "health": "microbe",
}


def _post(server: str, payload: dict) -> None:
    response = requests.post(server.rstrip("/") + "/", json=payload, timeout=15)
    response.raise_for_status()


def send_story(story: Story, *, server: str, topic: str, site_url: str) -> None:
    outlets = story.outlet_names()
    names = ", ".join(outlets[:4]) + (f" and {len(outlets) - 4} more" if len(outlets) > 4 else "")
    tags = [CATEGORY_TAGS[n] for n in story.category_names if n in CATEGORY_TAGS] or ["newspaper"]
    payload = {
        "topic": topic,
        "title": story.lead.title[:200],
        "message": f"{len(outlets)} outlets: {names}",
        "click": story.lead.link,
        "priority": 4 if story.category_names else 3,
        "tags": tags,
    }
    if site_url:
        payload["actions"] = [{"action": "view", "label": "Open Pulse", "url": site_url}]
    _post(server, payload)


def send_test(*, server: str, topic: str, site_url: str, top: Story | None) -> None:
    message = f"Top story right now: {top.lead.title}" if top else "No stories yet, but alerts are connected."
    payload = {
        "topic": topic,
        "title": "Pulse is connected",
        "message": message,
        "tags": ["white_check_mark"],
        "priority": 3,
    }
    if site_url:
        payload["click"] = site_url
    _post(server, payload)
