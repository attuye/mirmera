"""The browser's clock: exploring as if it were another day and time.

Date bugs hide at the edges of the calendar: just after midnight (when the user's day and the
server's UTC day differ), the last day of the year, 29 February, and in Ethiopia the thirteenth
month, Pagume, with 5 days or 6 in a leap year, and the New Year on Meskerem 1 (11 or 12
September). Mirmera sets the browser's clock to one of these moments (Playwright's clock: the
page's Date and timers start from it) in the user's time zone, so "today" is that day for the app.

It also knows the Ethiopian calendar, so a page that shows a date in it can be checked without
AI: the date sent, converted here, against the date the page shows.
"""
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Africa/Addis_Ababa"
EPOCH = 1724221  # the Julian day number of Meskerem 1, year 1 (Amete Mihret)
MONTHS = ("Meskerem", "Tikimt", "Hidar", "Tahsas", "Tir", "Yekatit", "Megabit", "Miazia", "Genbot", "Sene",
          "Hamle", "Nehase", "Pagume")
MONTHS_AM = ("መስከረም", "ጥቅምት", "ኅዳር", "ታኅሣሥ", "ጥር", "የካቲት", "መጋቢት", "ሚያዝያ", "ግንቦት", "ሰኔ", "ሐምሌ", "ነሐሴ", "ጳጉሜ")
SPELLINGS = {  # other ways the months are written
    "tikemt": 2, "tekemt": 2, "teqemt": 2, "hedar": 3, "tahisas": 4, "ter": 5, "yakatit": 6, "magabit": 7,
    "miyazya": 8, "miyazia": 8, "ginbot": 9, "sane": 10, "hamle": 11, "nehasse": 12, "pagumen": 13, "pagumie": 13,
    "ህዳር": 3, "ታህሳስ": 4, "ሐምሌ": 11, "ሀምሌ": 11, "ጳጉሜን": 13,
}

PRESETS = {
    "after-midnight": "just after midnight (the user's day is a day ahead of UTC)",
    "before-midnight": "just before midnight",
    "pagume": "the last day of Pagume, the Ethiopian thirteenth month",
    "ethiopian-new-year": "just after midnight on Ethiopian New Year (Meskerem 1)",
    "year-end": "New Year's Eve, just before midnight",
    "leap-day": "29 February",
}


# ---------- the Ethiopian calendar ----------

def _jdn(d: date) -> int:
    return d.toordinal() + 1721425


def is_leap(year: int) -> bool:
    """An Ethiopian leap year has a Pagume of 6 days (the year before a Gregorian leap year)."""
    return year % 4 == 3


def to_ethiopian(d: date) -> tuple[int, int, int]:
    """(year, month 1-13, day) in the Ethiopian calendar."""
    j = _jdn(d) - (EPOCH - 365)  # counted from a year before year 1, so each 4-year cycle ends on its leap year
    r = j % 1461
    n = r % 365 + 365 * (r // 1460)
    return 4 * (j // 1461) + r // 365 - r // 1460, n // 30 + 1, n % 30 + 1


def from_ethiopian(year: int, month: int, day: int) -> date:
    if not exists(year, month, day):
        raise ValueError(f"{name(year, month, day)} doesn't exist")
    j = EPOCH + 365 * (year - 1) + year // 4 + 30 * (month - 1) + day - 1
    return date.fromordinal(j - 1721425)


def exists(year: int, month: int, day: int) -> bool:
    if not (1 <= month <= 13 and day >= 1):
        return False
    return day <= (30 if month < 13 else 6 if is_leap(year) else 5)


def name(year: int, month: int, day: int) -> str:
    return f"{MONTHS[month - 1] if 1 <= month <= 13 else f'month {month}'} {day}, {year}"


def pagume_end(year: int) -> date:
    """The last day of Pagume of this Ethiopian year."""
    return from_ethiopian(year, 13, 6 if is_leap(year) else 5)


_MONTH_WORDS = {m.lower(): i + 1 for i, m in enumerate(MONTHS)} | {m: i + 1 for i, m in enumerate(MONTHS_AM)} | SPELLINGS
_MONTH_RE = "|".join(sorted((re.escape(w) for w in _MONTH_WORDS), key=len, reverse=True))
SHOWN = re.compile(rf"(?:\b|(?<=\s))(?P<m1>{_MONTH_RE})\s+(?P<d1>\d{{1,2}}),?\s+(?P<y1>\d{{4}})"
                   rf"|\b(?P<d2>\d{{1,2}})\s+(?P<m2>{_MONTH_RE})\s+(?P<y2>\d{{4}})", re.I)


def shown_dates(text: str) -> list[tuple[str, int, int, int]]:
    """Ethiopian dates written on a page: (as written, year, month, day)."""
    out = []
    for m in SHOWN.finditer(text):
        month = _MONTH_WORDS[(m["m1"] or m["m2"]).lower()]
        out.append((m.group(0).strip(), int(m["y1"] or m["y2"]), month, int(m["d1"] or m["d2"])))
    return out


def wrong_dates(text: str, sent: dict[str, str]) -> list[dict]:
    """Ethiopian dates on the page that are wrong: a day that doesn't exist (Pagume 6 in a year that
    isn't a leap year, a 31st), or, when one date was sent, a near date that isn't it in the
    Ethiopian calendar. sent: field label -> "YYYY-MM-DD". [{"shown", "why", "field"?}]"""
    out = []
    iso = {k: v for k, v in sent.items() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or "")}
    wanted = {}
    for label, v in iso.items():
        try:
            wanted[label] = to_ethiopian(date.fromisoformat(v))
        except ValueError:
            pass
    for written, y, m, d in shown_dates(text):
        if not exists(y, m, d):
            why = (f"Pagume has only 5 days in {y} (it isn't a leap year)" if m == 13 and d == 6
                   else f"{MONTHS[m - 1]} has only {6 if m == 13 else 30} days" if m == 13 or d > 30 else "no such day")
            out.append({"shown": written, "why": f"{written} doesn't exist: {why}"})
            continue
        if len(wanted) != 1:
            continue
        label, (wy, wm, wd) = next(iter(wanted.items()))
        if (y, m, d) == (wy, wm, wd):
            continue
        shown_day = from_ethiopian(y, m, d)
        sent_day = date.fromisoformat(iso[label])
        if abs((shown_day - sent_day).days) <= 3:  # near: the same date, converted wrong
            out.append({"shown": written, "field": label,
                        "why": f"{label} {iso[label]} is {name(wy, wm, wd)} in the Ethiopian calendar, not {written}"})
    return out


# ---------- the browser's clock ----------

def resolve(clock: str | None, tz: str | None = None, now: datetime | None = None) -> datetime | None:
    """The moment the browser's clock starts at, in the time zone tz: a preset name, or a local
    date and time ("2026-09-10T23:50"). None when no clock is set."""
    if not clock:
        return None
    zone = ZoneInfo(tz or DEFAULT_TZ)
    today = (now or datetime.now(zone)).astimezone(zone).date()
    at = lambda d, h, m: datetime.combine(d, time(h, m), zone)  # noqa: E731
    if clock == "after-midnight":
        return at(today, 0, 20)
    if clock == "before-midnight":
        return at(today, 23, 50)
    if clock in ("pagume", "ethiopian-new-year"):
        ey = to_ethiopian(today)[0]
        end = pagume_end(ey)
        if end < today:
            end = pagume_end(ey + 1)
        return at(end, 10, 0) if clock == "pagume" else at(end + timedelta(days=1), 0, 20)
    if clock == "year-end":
        return at(date(today.year, 12, 31), 23, 50)
    if clock == "leap-day":
        y = today.year
        while not (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) or date(y, 2, 29) < today:
            y += 1
        return at(date(y, 2, 29), 10, 0)
    try:
        return datetime.fromisoformat(clock).replace(tzinfo=zone)
    except ValueError:
        raise ValueError(f'"{clock}" is not a clock Mirmera knows: one of {", ".join(PRESETS)}, or a date and time like 2026-09-10T23:50')


def describe(start: datetime) -> str:
    """'Thu 10 Sep 2026, 23:50 (Africa/Addis_Ababa), Pagume 5, 2018 in the Ethiopian calendar'"""
    ey, em, ed = to_ethiopian(start.date())
    return f"{start:%a %d %b %Y, %H:%M} ({start.tzinfo}), {name(ey, em, ed)} in the Ethiopian calendar"


def date_ideas(today: date) -> list[tuple[str, str]]:
    """Dates near the calendar's edges, for a date field when the run has a clock."""
    ey = to_ethiopian(today)[0]
    end = pagume_end(ey) if pagume_end(ey) >= today else pagume_end(ey + 1)
    return [("yesterday (by the user's clock)", (today - timedelta(days=1)).isoformat()),
            ("the last day of Pagume", end.isoformat()),
            ("Ethiopian New Year (Meskerem 1)", (end + timedelta(days=1)).isoformat())]


async def install(context, start: datetime) -> None:
    """Sets the clock for every page of this browser context, before any page opens."""
    await context.clock.install(time=start)
