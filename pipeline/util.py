"""Small helpers shared by the pipeline."""
from __future__ import annotations

import datetime as dt
import html
import re

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def clean_text(value) -> str:
    """Strip HTML tags and entities, collapse whitespace."""
    if not value:
        return ""
    text = html.unescape(_TAG.sub(" ", str(value)))
    return _SPACE.sub(" ", text).strip()


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def to_iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def from_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def outlet_key(name: str) -> str:
    """Normalise outlet names so 'The Times of India' and 'Times of India' count once."""
    key = name.lower().strip()
    if key.startswith("the "):
        key = key[4:]
    for suffix in (".co.in", ".com", ".in"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    return _NON_ALNUM.sub("", key)
