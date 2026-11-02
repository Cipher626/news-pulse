"""Small helpers shared by the pipeline."""
from __future__ import annotations

import datetime as dt
import html
import re
import unicodedata
from urllib.parse import urlparse

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")

# Scripts we tokenise besides Latin. Add a range here to support another language.
DEVANAGARI = "\u0900-\u097f"
GUJARATI = "\u0a80-\u0aff"
WORD_CHARS = f"a-z0-9{DEVANAGARI}{GUJARATI}"

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Nukta and chandrabindu are spelled inconsistently across Hindi/Gujarati outlets.
_INDIC_FOLD = str.maketrans({"\u093c": None, "\u0abc": None, "\u0901": "\u0902", "\u0a81": "\u0a82"})


def clean_text(value) -> str:
    """Strip HTML tags and entities, collapse whitespace."""
    if not value:
        return ""
    text = html.unescape(_TAG.sub(" ", str(value)))
    return _SPACE.sub(" ", text).strip()


def normalize(text: str) -> str:
    """Lowercase and fold spelling variants so matching works across outlets and scripts."""
    return unicodedata.normalize("NFC", text).lower().translate(_INDIC_FOLD)


def detect_lang(text: str) -> str:
    deva = sum(1 for c in text if "\u0900" <= c <= "\u097f")
    guj = sum(1 for c in text if "\u0a80" <= c <= "\u0aff")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    if guj > max(deva, latin):
        return "gu"
    if deva > latin:
        return "hi"
    return "en"


def url_parts(url: str) -> tuple[str, list[str]]:
    """Host and lowercase path segments: https://sports.ndtv.com/cricket/x -> ('sports.ndtv.com', ['cricket', 'x'])."""
    parsed = urlparse(url)
    return parsed.netloc.lower(), [s.lower() for s in parsed.path.split("/") if s]


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
