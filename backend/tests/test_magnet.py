"""Bug Magnet's lists: the right ones for each field, after the obvious ideas, and typed out for testers."""
from datetime import date

from app.agent import magnet
from app.agent.explore import expand, ideas_for


def field(label, kind="text"):
    return {"label": label, "type": kind}


def test_each_field_gets_its_lists():
    assert magnet.lists_for(field("Email", "email")) == ["emails"]
    assert magnet.lists_for(field("Mobile number", "tel")) == ["phones"]
    assert magnet.lists_for(field("Full name")) == ["names", "text", "injection"]
    assert magnet.lists_for(field("Comment")) == ["text", "injection"]
    assert magnet.lists_for(field("Date of birth", "date")) == ["dates"]
    for kind in ("select", "checkbox", "password", "file"):
        assert magnet.lists_for(field("Anything", kind)) == []


def test_they_come_after_the_obvious_ideas_and_never_twice():
    ideas = ideas_for(field("Full name"), date(2026, 11, 21))
    values = [v for _, v in ideas]
    assert len(values) == len(set(values))
    first_magnet = next(i for i, (w, _) in enumerate(ideas) if w.startswith("Bug Magnet: "))
    assert all(w.startswith("Bug Magnet: ") for w, _ in ideas[first_magnet:])
    assert values.index("O'Brien-Smith") < first_magnet  # the name ideas Mirmera always had come first
    assert "Zoë Saldaña" in values and "' OR '1'='1" in values


def test_the_probes_show_a_problem_without_causing_one():
    every = [v for _, _, vals in magnet.CATEGORIES for _, v in vals]
    assert not any(w in v.upper() for v in every for w in ("DROP ", "DELETE ", "<SCRIPT", "ONERROR", "ALERT("))


def test_the_catalog_is_typed_out_for_testers(client):
    lists = client.get("/bug-magnet").json()
    assert [l["key"] for l in lists] == ["names", "text", "injection", "emails", "numbers", "dates", "phones"]
    names = next(l for l in lists if l["key"] == "names")["values"]
    long = next(v for v in names if v["code"] == "@count1000")
    assert len(long["value"]) == 1000 and long["value"].endswith("1000*")
    assert long["value"] == expand("@count1000")
    email = next(v for v in next(l for l in lists if l["key"] == "emails")["values"] if v["code"])
    assert email["value"] == "x" * 64 + "@example.com"


def test_they_are_counted_apart_from_the_test_ideas():
    """40 extra values must not make "6 of 14 ideas tried" look like "6 of 54"."""
    from app.agent.explore import ideas_text, magnet_tally
    f = field("Full name")
    today = date(2026, 11, 21)
    _, done, total = ideas_text([f], {}, today)
    every = ideas_for(f, today)
    assert total == len([w for w, _ in every if not w.startswith("Bug Magnet: ")])
    assert magnet_tally([f], {"Full name": {"Zoë Saldaña", "O'Brien-Smith"}}, today) == (1, len(every) - total)


def test_a_date_picker_gets_only_real_dates():
    """31 April can't be typed into <input type=date>: offering it wasted steps (Locator.fill: Malformed value)."""
    picker = [v for _, v in magnet.ideas(field("Expiry date", "date"))]
    assert picker == ["1900-01-01", "9999-12-31"]
    typed = [v for _, v in magnet.ideas(field("Expiry date (DD/MM/YYYY)"))]  # a plain text box takes anything
    assert "2026-04-31" in typed and "21/11/2026" in typed
