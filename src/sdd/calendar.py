"""Observation dates for a panel.

A ``Calendar`` in the spec says "start here, produce this many cut-offs, at this
frequency". This module turns that into the actual list of dates and into the
filename each one is written under.
"""

from __future__ import annotations

import pandas as pd

from sdd.spec.schema import Calendar

_OFFSETS = {
    "month_end": pd.offsets.MonthEnd,
    "month_start": pd.offsets.MonthBegin,
    "quarter_end": pd.offsets.QuarterEnd,
    "year_end": pd.offsets.YearEnd,
}


def period_dates(cal: Calendar) -> list[pd.Timestamp]:
    """Every cut-off date, first one included.

    The start date is snapped to the frequency's boundary — a calendar starting
    ``2024-01-15`` with ``month_end`` produces ``2024-01-31`` first, matching how
    loan tapes are actually cut.
    """
    base = pd.Timestamp(cal.start)
    if cal.freq == "day":
        return [base + pd.Timedelta(days=i) for i in range(cal.periods)]

    offset_cls = _OFFSETS[cal.freq]
    first = base + offset_cls(0)
    dates = [first]
    for _ in range(1, cal.periods):
        dates.append(dates[-1] + offset_cls(1))
    return dates


def format_filename(template: str, date: pd.Timestamp, period: int, name: str) -> str:
    """Fill a per-period filename template.

    Placeholders: ``{name} {yyyy} {mm} {dd} {yyyymm} {yyyy_mm_dd} {period}``.
    """
    return template.format(
        name=name,
        yyyy=f"{date.year:04d}",
        mm=f"{date.month:02d}",
        dd=f"{date.day:02d}",
        yyyymm=f"{date.year:04d}{date.month:02d}",
        yyyy_mm_dd=date.strftime("%Y-%m-%d"),
        period=period,
    )


def first_business_day(year: int, month: int = 1) -> pd.Timestamp:
    """First weekday of a month — the ESMA convention for a deal closing date."""
    d = pd.Timestamp(year=year, month=month, day=1)
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d += pd.Timedelta(days=1)
    return d
