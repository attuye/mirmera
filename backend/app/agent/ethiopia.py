"""The Ethiopia pack: test ideas and one check for apps whose users are in Ethiopia (a project setting).

Ideas, by field: phone numbers in every format people here type (+251 9…, 09…, Safaricom's 07…, 251…
without the plus, with spaces); names as they are written here (a given name and a father's name with no
surname, with a grandfather's name, "W/Mariam" for Wolde Mariam, Afaan Oromo with doubled vowels and an
apostrophe); amounts in Birr (thousands separators, santim, "ብር"); addresses with Woreda and Kebele.

The check, without AI: two valid forms of the same kind of value, the rest of the form the same, and the app
accepts one and refuses the other (+251911223344 accepted, 0711223344 refused). Users type both, so refusing
one is a bug unless a rule says the field takes only that format.
"""
import re
from contextvars import ContextVar

ON: ContextVar[bool] = ContextVar("ethiopia_pack", default=False)

PHONES = [  # (what, value): all valid, the same subscriber written the ways people write it
    ("an Ethio telecom number, international (+251 9…)", "+251911223344"),
    ("a local Ethio telecom number (09…)", "0911223344"),
    ("a Safaricom Ethiopia number (07…)", "0711223344"),
    ("international without the plus (251…)", "251911223344"),
    ("with spaces (+251 91 122 3344)", "+251 91 122 3344"),
]
BAD_PHONES = [("an Ethiopian number one digit short", "091122334")]
NAMES = [
    ("a given name and a father's name (no surname)", "Abebe Kebede"),
    ("with a grandfather's name", "Abebe Kebede Tesfaye"),
    ("W/ for Wolde (Haile W/Mariam)", "Haile W/Mariam"),
    ("Afaan Oromo, doubled vowels (Caaltuu Gammachuu)", "Caaltuu Gammachuu"),
    ("Afaan Oromo with an apostrophe (Bal'ina Dabalaa)", "Bal'ina Dabalaa"),
    ("in Ge'ez letters (ሰላማዊት ተስፋዬ)", "ሰላማዊት ተስፋዬ"),
]
AMOUNTS = [
    ("Birr with a thousands separator (1,250.50)", "1,250.50"),
    ("santim only (0.50)", "0.50"),
    ("a large amount (1,000,000)", "1,000,000"),
]
ADDRESSES = [
    ("an Addis Ababa address with sub-city, Woreda and house number", "Bole, Woreda 03, House No. 1234, Addis Ababa"),
    ("a Kebele address in a regional town", "Kebele 07, Hawassa"),
    ("in Ge'ez letters", "ቦሌ ወረዳ 03፣ አዲስ አበባ"),
]
VALID = {"phone": {v for _, v in PHONES}, "name": {v for _, v in NAMES}, "amount": {v for _, v in AMOUNTS},
         "address": {v for _, v in ADDRESSES}}
FORMAT_RULE = re.compile(r"\b(start(?:s|ing)? with|format|only|must be in|digits|latin|english letters|letters only)\b", re.I)


def kind_of(f: dict) -> str | None:
    label, t = (f.get("label") or "").lower(), f.get("type", "")
    if t == "tel" or re.search(r"phone|mobile|telephone|ስልክ", label):
        return "phone"
    if t in ("text", "") and re.search(r"\bname\b|ስም|maqaa", label) and not re.search(r"user ?name|login|company|business", label):
        return "name"
    if re.search(r"amount|price|birr|total|salary|payment|ብር|መጠን", label) and t in ("text", "number", ""):
        return "amount"
    if re.search(r"address|street|kebele|woreda|city|town|አድራሻ", label) and t in ("text", "textarea", ""):
        return "address"
    return None


def ideas(f: dict) -> list[tuple[str, str]]:
    """The pack's ideas for this field (none when the project's users aren't in Ethiopia)."""
    if not ON.get():
        return []
    k = kind_of(f)
    if k == "phone":
        return PHONES[1:] + BAD_PHONES  # the first is the baseline Mirmera fills anyway
    if k == "name":
        return NAMES[1:]
    if k == "amount":
        if f.get("type") == "number":
            return [(w, v.replace(",", "")) for w, v in AMOUNTS]  # a number field has no separators to type
        return AMOUNTS
    if k == "address":
        return ADDRESSES
    return []


def _valid(f: dict, value: str) -> bool:
    k = kind_of(f)
    if not k:
        return False
    v = (value or "").strip()
    return v in VALID[k] or (k == "amount" and v.replace(",", "") in {x.replace(",", "") for x in VALID[k]})


def check(fields: list[dict], values: dict, outcome: str, submits: list, rules: dict[str, str], said: list[str]):
    """After a refused submit: an earlier accepted submit of the same form that differs in one field only,
    both values valid Ethiopian forms. Returns a bug draft, or None."""
    if not ON.get() or outcome != "refused" or not said:
        return None
    from app import schemas
    for prev_values, prev_outcome, prev_n in submits:
        if prev_outcome != "accepted" or set(prev_values) != set(values):
            continue
        diff = [k for k in values if values[k] != prev_values[k]]
        if len(diff) != 1:
            continue
        label = diff[0]
        f = next((x for x in fields if x["label"] == label), None)
        if not f or not _valid(f, values[label]) or not _valid(f, prev_values[label]):
            continue
        words = {w for w in re.findall(r"\w+", label.lower()) if len(w) >= 4}
        if any(FORMAT_RULE.search(t) and words & set(re.findall(r"\w+", t.lower())) for t in rules.values()):
            continue  # a rule says the field takes one format: refusing the other is what it asks
        what = next((w for w, v in PHONES + NAMES + AMOUNTS + ADDRESSES if v == values[label]), "this form")
        return schemas.ExploreProblem(
            title=f'{label} "{values[label][:40]}" ({what.split(" (")[0]}) is refused although "{prev_values[label][:40]}" is accepted'[:200],
            severity="medium",
            expected=f'Both are valid ways people in Ethiopia write a {kind_of(f)}: with everything else the same, the app '
                     f'accepted "{prev_values[label]}" (step {prev_n}), so it accepts "{values[label]}" too, or turns it into its own format.',
            actual=f'With {label} = "{values[label]}", the app answered: "{said[0][:200]}".'[:600],
            symptom=schemas.Symptom(kind="text_visible", value=said[0][:300],
                                    explanation=f"A valid Ethiopian {kind_of(f)} refused"))
    return None
