"""Display versus data: is what the page shows the same as what the server sent?

From James and Jon Bach's Mecha-Testing: separate the display layer from the data layer, and ask of each
whether it is right. Every screen is drawn from JSON the server sent; the agent's browser keeps both, so
this compares them in plain code, with no AI and no story rules:

- ``mismatches(answers, page_text)``: a value the page shows under a label (``Expiry date: 29/11/2026``)
  that differs from the same field in a JSON answer (``expiryDate: "2026-11-30"``). A bug draft: the
  product contradicts itself. Careful by design: only a label that clearly names the field counts, only a
  value that reads as the same kind (a date, a number, a status) is compared, and formatting is never a
  difference (PASSPORT is Passport, 1500 is 1,500.00, 30/11/2026 is 2026-11-30).
- ``curios(answers, today)``: things inside the data that may be wrong (a valid status with an expiry
  that has passed, a few records in a list without a field the rest have). Session notes for the tester
  to judge; Mirmera can't tell on its own.
"""
import re
from datetime import date

POSITIVE = {"verified", "approved", "accepted", "valid", "success", "succeeded", "passed", "active", "complete",
            "completed", "confirmed", "ok", "true", "yes", "cleared"}
NEGATIVE = {"rejected", "declined", "denied", "failed", "failure", "invalid", "expired", "blocked", "refused",
            "error", "false", "no", "suspended", "revoked", "fraud", "flagged"}
PENDING = {"pending", "processing", "submitted", "review", "in_review", "under_review", "queued", "waiting",
           "in_progress", "awaiting"}
STATUS_KEYS = re.compile(r"(^|_)(status|state|result|decision|verdict|outcome)$|^(is_?)?(verified|approved|valid|accepted|rejected)$", re.I)
ID_KEYS = re.compile(r"(number|no|reference|ref|code)$", re.I)  # identifiers: shown as they are
SKIP_KEYS = re.compile(r"(^|_)(id|uuid|url|href|token|hash|etag|version|created_?at|updated_?at)$", re.I)
SYNONYMS = {"dob": "date of birth", "doc": "document", "exp": "expiry", "fname": "first name", "lname": "last name"}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MOJIBAKE = re.compile(r"Ã[\x80-\xbf]|â€|\ufffd")  # UTF-8 read as Latin-1 / Windows-1252, or a replacement character


# ---------- reading the JSON ----------

def words(key: str) -> list[str]:
    """expiryDate, expiry_date, ExpiryDate → [expiry, date]; dob → [date, of, birth]."""
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).replace("_", " ").replace("-", " ").lower().split()
    out = []
    for p in parts:
        out += SYNONYMS.get(p, p).split()
    return out


def leaves(data, path=""):
    """(path, key, value) for each plain value, skipping lists of several records (a table, not one thing)."""
    if isinstance(data, dict):
        for k, v in data.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, (dict, list)):
                yield from leaves(v, p)
            else:
                yield p, k, v
    elif isinstance(data, list) and len(data) == 1:
        yield from leaves(data[0], f"{path}[0]")


def records(data, path=""):
    """Every dict in the data, with where it is."""
    if isinstance(data, dict):
        yield path, data
        for k, v in data.items():
            yield from records(v, f"{path}.{k}" if path else k)
    elif isinstance(data, list):
        for i, v in enumerate(data[:500]):
            yield from records(v, f"{path}[{i}]")


def lists(data, path=""):
    """Lists of records (dicts), with where they are."""
    if isinstance(data, list):
        if len(data) >= 5 and all(isinstance(x, dict) for x in data):
            yield path or "(the answer)", data
        for i, v in enumerate(data[:50]):
            yield from lists(v, f"{path}[{i}]")
    elif isinstance(data, dict):
        for k, v in data.items():
            yield from lists(v, f"{path}.{k}" if path else k)


# ---------- reading values ----------

def as_date(value) -> date | None:
    """A JSON date: 2026-11-30, or a timestamp's day (2026-11-30T00:00:00Z)."""
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})([T ][\d:.]+(Z|[+-]\d{2}:?\d{2})?)?", value.strip())
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def has_time(value: str) -> bool:
    return bool(re.search(r"[T ]\d{2}:\d{2}", value))


def shown_dates(text: str) -> set[date]:
    """Every day a shown value could mean: 03/04/2026 is 3 April or 4 March, both kept."""
    text = text.strip().lower()
    out: set[date] = set()

    def add(y, m, d):
        try:
            out.add(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    if m := re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", text):
        add(m[1], m[2], m[3])
    elif m := re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", text):
        add(m[3], m[2], m[1])
        add(m[3], m[1], m[2])
    elif m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})\b", text):
        if m[2] in MONTHS:
            add(m[3], MONTHS[m[2]], m[1])
    elif m := re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", text):
        if m[1] in MONTHS:
            add(m[3], MONTHS[m[1]], m[2])
    return out


def shown_numbers(text: str) -> set[float]:
    """1,234.50 and 1.234,50 and 1 234,50 all read as 1234.5; a bare 1,5 as 1.5 or 15."""
    m = re.search(r"-?\d[\d\s,.' ]*", text)
    if not m:
        return set()
    raw = re.sub(r"[\s' ]", "", m[0]).rstrip(".,")
    out = set()
    for dec, grp in ((".", ","), (",", ".")):
        s = raw.replace(grp, "")
        if s.count(dec) <= 1:
            try:
                out.add(float(s.replace(dec, ".")))
            except ValueError:
                pass
    return out


def polarity(value) -> str | None:
    """positive, negative or pending: what a status (or a yes/no) means."""
    if isinstance(value, bool):
        return "positive" if value else "negative"
    if not isinstance(value, str):
        return None
    v = re.sub(r"[\s-]+", "_", value.strip().lower())
    tokens = v.split("_")
    if tokens[0] == "not" or (v.startswith("un") and v[2:] in POSITIVE):
        return "negative"
    for name, group in (("pending", PENDING), ("positive", POSITIVE), ("negative", NEGATIVE)):
        if v in group or any(t in group for t in tokens):
            return name
    return None


def shown_polarity(text: str) -> str | None:
    t = text.strip().lower()
    if re.match(r"^(not|un)\s*\w", t):
        return "negative"
    for w in re.findall(r"[a-z_]+", t)[:3]:
        p = polarity(w)
        if p:
            return p
    return None


def garbled(s: str) -> bool:
    """UTF-8 text read as Windows-1252 or Latin-1: "አበበ" shown as "áŠ á‰ á‰ "."""
    if MOJIBAKE.search(s):
        return True
    for enc in ("cp1252", "latin-1"):
        try:
            fixed = s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed != s:
            return True
    return False


def plain(s: str) -> str:
    return re.sub(r"[\s_\-./]", "", s).casefold()


# ---------- the page ----------

def labelled(page_text: str) -> list[tuple[str, str]]:
    """(label, value) pairs as a page shows them: "Expiry date: 29/11/2026", a label line then its value,
    or a table row "Expiry date<TAB>29/11/2026"."""
    lines = [ln.strip() for ln in page_text.splitlines()]
    lines = [ln for ln in lines if ln]
    out = []
    for i, ln in enumerate(lines):
        if m := re.match(r"^([^:\t]{2,40})[:\t]\s*(.+)$", ln):
            out.append((m[1].strip(), m[2].strip()))
        elif len(ln) <= 40 and i + 1 < len(lines) and not re.search(r"\d", ln):
            out.append((ln, lines[i + 1]))
    return out


def label_matches(label: str, key_words: list[str]) -> bool:
    lw = re.findall(r"[a-z0-9]+", label.lower())
    kw = [w for w in key_words if w not in ("is", "the")]
    return bool(kw) and lw == kw or (len(kw) > 1 and lw[-len(kw):] == kw and len(lw) <= len(kw) + 2)


# ---------- 1. the screen against the data ----------

def compare(key: str, value, shown: str) -> str | None:
    """Why the shown value differs from the data, or None when it's the same (or can't be compared)."""
    if value is None or value == "":
        return None
    d = as_date(value)
    if d:
        days = shown_dates(shown)
        if not days or d in days:
            return None
        nearest = min(days, key=lambda x: abs((x - d).days))
        off = (nearest - d).days
        if abs(off) == 1:
            if has_time(value):
                return None  # a timestamp's day can differ by time zone: that's the zone, not a bug
            return f"one day {'later' if off > 0 else 'earlier'}: a date without a time, moved by a time-zone conversion"
        return "a different date"
    if STATUS_KEYS.search(key) or isinstance(value, bool):
        p, q = polarity(value), shown_polarity(shown)
        if p and q and p != q:
            return f"the data says {p}, the page says {q}"
        return None
    if isinstance(value, (int, float)):
        nums = shown_numbers(shown)
        if not nums or any(abs(n - value) < 0.005 for n in nums):
            return None
        if any(n and abs(value / n) in (10, 100, 1000) for n in nums):
            return "off by a factor of 10, 100 or 1,000 (a unit, cents or a decimal point)"
        return "a different number"
    if isinstance(value, str):
        if garbled(shown) and not garbled(value):
            return "garbled: the text's encoding was read wrong (UTF-8 shown as Latin-1)"
        if ID_KEYS.search(key) and plain(shown) != plain(value):
            if plain(value).startswith(plain(shown)) and len(plain(shown)) >= 3:
                return "cut short"
            if re.fullmatch(r"[\w\- /.]{2,40}", shown) and not re.search(r"\s", shown.strip()):
                return "a different identifier"
    return None


def mismatches(answers: list[dict], page_text: str) -> list[dict]:
    """Shown values that contradict the JSON answers: {label, shown, key, value, why, url, method}."""
    pairs = labelled(page_text)
    found, seen = [], set()
    for a in answers:
        for path, key, value in leaves(a["data"]):
            if SKIP_KEYS.search(key) or not isinstance(value, (str, int, float, bool)):
                continue
            kw = words(key)
            for label, shown in pairs:
                if not label_matches(label, kw):
                    continue
                why = compare(key, value, shown)
                if why and label.lower() not in seen:
                    seen.add(label.lower())
                    found.append({"label": label, "shown": shown[:80], "key": path, "value": value, "why": why,
                                  "url": a["url"], "method": a.get("method", "GET")})
                break
    return found


# ---------- 2. curious things inside the data ----------

def curios(answers: list[dict], today: date) -> list[dict]:
    """{what, url, key}: things in the data that may be wrong. Never more than a few per answer."""
    out = []
    for a in answers:
        mine = []
        for path, rec in records(a["data"]):
            where = path or "the answer"
            dates = {k: as_date(v) for k, v in rec.items() if as_date(v)}
            status = next((v for k, v in rec.items() if STATUS_KEYS.search(k) and polarity(v) == "positive"), None)
            for k, d in dates.items():
                kl = k.lower()
                if "expir" in kl and status is not None and d < today:
                    mine.append((f"{where}: status \"{status}\" but {k} {rec[k]} has passed", f"expired:{k}"))
                if ("birth" in kl or kl == "dob") and d > today:
                    mine.append((f"{where}: {k} {rec[k]} is in the future", f"future:{k}"))
                if "issue" in kl:
                    for k2, d2 in dates.items():
                        if "expir" in k2.lower() and d > d2:
                            mine.append((f"{where}: {k} {rec[k]} is after {k2} {rec[k2]}", f"order:{k}"))
        for path, items in lists(a["data"]):
            keys = {k for x in items for k in x}
            for k in sorted(keys):
                has = [x for x in items if x.get(k) not in (None, "")]
                if 0.8 <= len(has) / len(items) < 1:
                    mine.append((f"{len(items) - len(has)} of {len(items)} records in {path} have no {k}", f"missing:{path}.{k}"))
            for k in ("id", "_id", "uuid"):
                ids = [x.get(k) for x in items if x.get(k) is not None]
                dups = sorted({str(i) for i in ids if ids.count(i) > 1})
                if dups:
                    mine.append((f"the same {k} twice in {path}: {', '.join(dups[:3])}", f"dup:{path}.{k}"))
        for what, key in mine[:4]:
            out.append({"what": what, "key": key, "url": a["url"], "method": a.get("method", "GET")})
    return out


def answer_of(url: str, method: str, body: bytes, content_type: str) -> dict | None:
    """A JSON answer worth comparing: small enough, really JSON, an object or a list."""
    import json
    if "json" not in (content_type or "").lower() or len(body) > 1_000_000:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return {"url": url, "method": method, "data": data} if isinstance(data, (dict, list)) else None
