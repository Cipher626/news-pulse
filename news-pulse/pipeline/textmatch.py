"""Keyword matching that works for English, Hindi and Gujarati.

English keywords match whole words (plus a plural s/es). Hindi/Gujarati keywords of four or more
characters also match as a word prefix, because Gujarati glues postpositions
onto words (ગુજરાત + માં = ગુજરાતમાં).
"""
from __future__ import annotations

import re

from .util import WORD_CHARS, normalize

_BEFORE = rf"(?<![{WORD_CHARS}])"
_AFTER = rf"(?![{WORD_CHARS}])"


def phrase_regex(words: list[str]) -> re.Pattern | None:
    parts = []
    for word in {normalize(w.strip()) for w in words if w and w.strip()}:
        if word.isascii():
            plural = "(?:s|es)?" if word[-1].isalpha() else ""
            parts.append(_BEFORE + re.escape(word) + plural + _AFTER)
        elif len(word) < 4:
            parts.append(_BEFORE + re.escape(word) + _AFTER)
        else:
            parts.append(_BEFORE + re.escape(word))
    if not parts:
        return None
    parts.sort(key=len, reverse=True)
    return re.compile("|".join(parts))


def matches(rx: re.Pattern | None, text: str) -> bool:
    return bool(rx and rx.search(normalize(text)))
