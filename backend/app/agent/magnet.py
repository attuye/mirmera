"""Bug Magnet's idea, in Mirmera: a library of the values that tend to break forms, by kind of field.

Gojko Adzic's Bug Magnet (a browser extension James Bach lists among his free tools) gives testers a menu of
tricky inputs: names from other scripts, emails that are valid but odd, numbers at the edges, Unicode that
looks like nothing, text that is really markup. Here the same lists feed two places:

- the agent's test ideas, after the field's own rules, the team's values and the Ethiopia pack, so it reaches
  for them once the obvious ideas are done;
- the session's **Bug Magnet** tool, where a tester copies a value into the app under test and logs the try.

Each value says what it probes. They show a problem without causing one: a lone apostrophe or "' OR '1'='1"
finds unescaped SQL without deleting anything, and the markup probe makes text bold rather than running a
script, so a page that shows it bold has shown the bug. "@count300" and "@long1000" are typed out by Mirmera.
"""

CATEGORIES: list[tuple[str, str, list[tuple[str, str]]]] = [  # (key, title, [(what, value)])
    ("names", "Names", [
        ("Ge'ez and Latin in one name", "Abebe ከበደ"),
        ("accents (Zoë Saldaña)", "Zoë Saldaña"),
        ("Arabic, written right to left", "محمد عبد الله"),
        ("Chinese characters", "李小龙"),
        ("a single name, no surname", "Abebe"),
        ("an apostrophe only (O')", "O'"),
        ("a 1,000-character counterstring", "@count1000"),
    ]),
    ("text", "Text and Unicode", [
        ("a zero-width space inside (looks like 'ab')", "a​b"),
        ("a right-to-left override (shows reversed)", "‮abc"),
        ("an emoji family (one emoji, several code points)", "👨‍👩‍👧"),
        ("e and a combining accent (é in two code points)", "é"),
        ("a line break in the middle", "first line\nsecond line"),
        ("a tab in the middle", "before\tafter"),
        ("leading and trailing spaces", "  padded  "),
        ("a 300-character counterstring", "@count300"),
    ]),
    ("injection", "Markup and injection probes", [
        ("HTML that shows bold if not escaped", '"><b>bold if unescaped</b>'),
        ("a lone apostrophe (breaks unescaped SQL)", "'"),
        ("an always-true SQL condition", "' OR '1'='1"),
        ("a template expression (shows 49 if evaluated)", "{{7*7}}"),
        ("a path up the folders", "../../etc/passwd"),
        ("percent-encoding that should stay text", "%3Cb%3E"),
    ]),
    ("emails", "Emails", [
        ("a plus tag (valid)", "name+test@example.com"),
        ("a subdomain (valid)", "name@mail.example.co.uk"),
        ("upper case (valid; the same address)", "NAME@EXAMPLE.COM"),
        ("a quoted local part (valid)", '"john doe"@example.com'),
        ("two @ signs", "name@@example.com"),
        ("a space inside", "na me@example.com"),
        ("a trailing dot", "name@example.com."),
        ("a 64-character local part (the longest valid)", "@long64@example.com"),
    ]),
    ("numbers", "Numbers", [
        ("minus zero", "-0"),
        ("a decimal comma (1,5)", "1,5"),
        ("a decimal point (1.5)", "1.5"),
        ("leading zeros", "007"),
        ("scientific notation", "1e3"),
        ("one past the 32-bit limit", "2147483648"),
        ("one past the safe JavaScript integer", "9007199254740993"),
        ("Ge'ez numerals (፩፪፫)", "፩፪፫"),
        ("Arabic-Indic digits (١٢٣)", "١٢٣"),
        ("not a number", "NaN"),
    ]),
    ("dates", "Dates", [
        ("29 February in a year without one", "2027-02-29"),
        ("31 April", "2026-04-31"),
        ("month 13 (also Pagume in the Ethiopian calendar)", "2026-13-01"),
        ("day first, as written here (21/11/2026)", "21/11/2026"),
        ("the first of 1900", "1900-01-01"),
        ("the last day there is", "9999-12-31"),
    ]),
    ("phones", "Phone numbers", [
        ("the country code twice", "+2510911223344"),
        ("with dashes", "091-122-3344"),
        ("with brackets", "(+251) 911 223 344"),
        ("an extension", "+251911223344 ext. 12"),
        ("a foreign number (Kenya)", "+254712345678"),
    ]),
]

KINDS = {  # which lists go with a field, by its type or a word in its label
    "email": ["emails"], "tel": ["phones"], "number": ["numbers"], "date": ["dates"],
}
WORDS = [("mail", "emails"), ("phone", "phones"), ("mobile", "phones"), ("name", "names"),
         ("amount", "numbers"), ("quantity", "numbers"), ("age", "numbers"), ("date", "dates"), ("birth", "dates")]


def lists_for(f: dict) -> list[str]:
    """The Bug Magnet lists for a field: its kind's list, then text and injection for anything typed."""
    kind, label = f.get("type", "text"), f.get("label", "").lower()
    if kind.startswith("select") or kind in ("checkbox", "radio", "password", "file"):
        return []
    keys = list(KINDS.get(kind, []))
    keys += [k for w, k in WORDS if w in label and k not in keys]
    if not keys or keys == ["names"]:
        keys += ["text", "injection"]
    return keys


def ideas(f: dict) -> list[tuple[str, str]]:
    """(what, value) from the lists for this field, labelled as Bug Magnet's. A date field (the browser's
    date picker) holds only real dates: 31 April or 21/11/2026 can't even be typed into it, so those are
    left out; they stay for date fields that are plain text."""
    by_key = {k: vals for k, _, vals in CATEGORIES}
    out = [(f"Bug Magnet: {what}", value) for key in lists_for(f) for what, value in by_key[key]]
    if f.get("type") == "date":
        out = [(w, v) for w, v in out if real_date(v)]
    return out


def real_date(v: str) -> bool:
    from datetime import date
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def catalog() -> list[dict]:
    """Every list, for the tester's tool: each value as it is typed (codes written out)."""
    from app.agent.explore import expand
    return [{"key": k, "title": t, "values": [{"what": w, "value": expand(v), "code": v if v.startswith("@") else None}
                                               for w, v in vals]}
            for k, t, vals in CATEGORIES]
