"""Read the workflow's cron line and work out the next runs, so the page tells the truth
about when the next update is due instead of guessing from a config number.

Supports the cron forms GitHub accepts: *, lists (7,37), ranges (1-5), steps (*/30, 0-20/5, 5/15).
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

_CRON = re.compile(r"""cron:\s*["']([^"']+)["']""")


def read_cron(workflow: Path) -> str | None:
    try:
        match = _CRON.search(workflow.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1).strip() if match else None


def _field(spec: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = int(part)
            end = hi if step > 1 else start
        values.update(range(start, end + 1, step))
    return values


def next_runs(expr: str, after: dt.datetime, count: int = 2) -> list[dt.datetime]:
    minute, hour, dom, month, dow = expr.split()
    minutes, hours = _field(minute, 0, 59), _field(hour, 0, 23)
    days, months = _field(dom, 1, 31), _field(month, 1, 12)
    weekdays = {d % 7 for d in _field(dow, 0, 7)}  # cron: 0 and 7 are Sunday
    dom_any, dow_any = dom == "*", dow == "*"

    t = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    found: list[dt.datetime] = []
    for _ in range(60 * 24 * 62):  # look ahead two months at most
        cron_dow = (t.weekday() + 1) % 7
        dom_ok, dow_ok = t.day in days, cron_dow in weekdays
        day_ok = (dom_ok or dow_ok) if not (dom_any or dow_any) else (dom_ok and dow_ok)
        if t.minute in minutes and t.hour in hours and t.month in months and day_ok:
            found.append(t)
            if len(found) == count:
                break
        t += dt.timedelta(minutes=1)
    return found
