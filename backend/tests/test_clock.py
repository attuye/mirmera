"""The browser clock's calendar: Ethiopian dates, the presets at the calendar's edges, and the API."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agent import clock


@pytest.mark.parametrize("greg, eth", [
    (date(2026, 9, 11), (2019, 1, 1)),   # Ethiopian New Year
    (date(2023, 9, 12), (2016, 1, 1)),   # New Year a day later, after a Pagume of 6 days
    (date(2023, 9, 11), (2015, 13, 6)),  # Pagume 6: 2015 is a leap year
    (date(2026, 9, 10), (2018, 13, 5)),  # Pagume 5: the last day of 2018
    (date(2026, 1, 7), (2018, 4, 29)),   # Genna
    (date(2024, 1, 7), (2016, 4, 28)),   # Genna in the year after a leap year
])
def test_ethiopian_dates(greg, eth):
    assert clock.to_ethiopian(greg) == eth
    assert clock.from_ethiopian(*eth) == greg


def test_every_day_for_two_centuries_converts_both_ways():
    d = date(1900, 1, 1)
    while d < date(2100, 1, 1):
        assert clock.from_ethiopian(*clock.to_ethiopian(d)) == d
        d += timedelta(days=1)


def test_pagume_has_six_days_only_in_a_leap_year():
    assert clock.exists(2015, 13, 6) and not clock.exists(2018, 13, 6) and not clock.exists(2018, 2, 31)
    with pytest.raises(ValueError):
        clock.from_ethiopian(2018, 13, 6)


def test_wrong_ethiopian_dates_on_a_page():
    sent = {"Delivery date": "2026-09-11"}
    assert clock.wrong_dates("We deliver on Meskerem 1, 2019.", sent) == []
    assert clock.wrong_dates("ቀን: 1 መስከረም 2019", sent) == []  # in Amharic, day first
    [late] = clock.wrong_dates("We deliver on Meskerem 2, 2019.", sent)
    assert late["why"] == "Delivery date 2026-09-11 is Meskerem 1, 2019 in the Ethiopian calendar, not Meskerem 2, 2019"
    [gone] = clock.wrong_dates("Pagume 6, 2018", {})
    assert "Pagume has only 5 days in 2018" in gone["why"]
    assert clock.wrong_dates("Hidar 3, 2010 and Tir 9, 2016", sent) == []  # far from the date sent: another date


def test_presets_are_moments_at_the_calendars_edges():
    now = datetime(2026, 10, 12, 15, 0, tzinfo=ZoneInfo("UTC"))
    at = lambda p: clock.resolve(p, "Africa/Addis_Ababa", now)  # noqa: E731
    assert at("after-midnight").isoformat() == "2026-10-12T00:20:00+03:00"
    assert at("before-midnight").isoformat() == "2026-10-12T23:50:00+03:00"
    assert at("pagume").date() == date(2027, 9, 11)  # Pagume 6, 2019: a leap year
    assert at("ethiopian-new-year").isoformat() == "2027-09-12T00:20:00+03:00"
    assert at("year-end").isoformat() == "2026-12-31T23:50:00+03:00"
    assert at("leap-day").date() == date(2028, 2, 29)
    assert clock.resolve("2026-09-10T23:50").isoformat() == "2026-09-10T23:50:00+03:00"
    assert clock.resolve(None) is None
    with pytest.raises(ValueError, match="after-midnight"):
        clock.resolve("tea time")


def test_a_run_with_a_clock_it_doesnt_know_is_refused(client):
    r = client.post("/charters/nope/explore", json={"conditions": {"clock": "tea time"}})
    assert r.status_code == 422 and "after-midnight" in r.text


def test_a_regression_test_sets_the_clock_it_was_found_with():
    from app.regression.render import render
    plan = {"title": "Past date accepted", "steps": [{"type": "goto", "url": "http://localhost:4200/order"}],
            "assertions": [], "clock": {"at": "2026-09-11T00:20:00+03:00", "timezone": "Africa/Addis_Ababa"}}
    code = render(plan, bug_title="x", oracle="claims", issue_links=[], provenance="mock", known_failing=False)
    assert "test.use({ timezoneId: 'Africa/Addis_Ababa' })" in code or 'test.use({ timezoneId: "Africa/Addis_Ababa" })' in code
    assert "await page.clock.setFixedTime(new Date(" in code
