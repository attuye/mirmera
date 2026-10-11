"""The second practice app, AddisMart (practice-app/shop.mjs): a shop whose six bugs were
fixed before Mirmera's sightings, general rule checks, flow tours and replays were built.

The ceiling here is an agent that does the obvious tester things and reports only what it
can see, with a check that never helps (it answers "other" to everything). What it finds
is what Mirmera finds itself: the rules it reads, the tours, the replays. The wrong total
is the one bug no check of Mirmera's is built for: the agent has to notice it.
"""
import json
import os
import socket
import subprocess
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.evals import explore_eval
from tests.test_explore import chromium_available  # noqa: F401  (fixture)

SHOP = Path(__file__).resolve().parents[2] / "practice-app" / "shop.mjs"


@pytest.fixture(scope="module")
def shop():
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        if os.environ.get("CI"):
            raise
        pytest.skip("Node isn't available to run the practice shop")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(["node", str(SHOP)], env={**os.environ, "PORT": str(port)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield url
    proc.terminate()


def step(thought, **action):
    return json.dumps({"thought": thought, "action": action})


class Obvious:
    """Per charter, the obvious tester steps; the check answers nothing useful."""
    name, model = "scripted", "obvious-shopper"

    def __init__(self, url):
        self.url, self.plan, self.i = url, [], 0

    def plan_for(self, mission: str) -> list[str]:
        order = step("The order form", kind="goto", url=f"{self.url}/order")
        valid = step("Valid first", kind="try", values={})
        done = step("Covered", kind="done", summary="Covered the charter's ideas.")
        m = mission.lower()
        if "quantit" in m:
            return [order, valid, order, step("One over the limit", kind="try", values={"Quantity": "11"}),
                    order, step("Three items", kind="try", values={"Quantity": "3"}),
                    step("3 × 350 Birr is 1050, not 700", kind="problem", problem={
                        "title": "The total for 3 × Coffee beans is 700 Birr instead of 1050", "severity": "high",
                        "expected": "The total is the price times the quantity: 3 × 350 = 1050 Birr.",
                        "actual": "The confirmation says total 700 Birr.",
                        "symptom": {"kind": "text_visible", "value": "total 700 Birr", "explanation": "3 × 350 is 1050"}}),
                    done]
        if "contact" in m:
            return [order, valid, order, step("No phone", kind="try", values={"Phone number": ""}),
                    step("No domain", kind="try", values={"Email": "name@"}), done]
        if "after placing" in m:
            return [order, valid, step("My orders", kind="goto", url=f"{self.url}/orders"), done]
        yesterday, today = date.today() - timedelta(days=1), date.today()
        return [order, valid, order, step("Yesterday", kind="try", values={"Delivery date": yesterday.isoformat()}),
                step("Today", kind="try", values={"Delivery date": today.isoformat()}), done]

    async def complete(self, system, user, **kwargs):
        if kwargs.get("task") == "explore_judge":
            return json.dumps({"answer_means": "other", "answer_quote": "", "rule": "", "rule_expects": "none"})
        if "(no steps yet)" in user:
            mission = json.loads(user.split("<charter>")[1].split("</charter>")[0])["mission"]
            self.plan, self.i = self.plan_for(mission), 0
        reply = self.plan[min(self.i, len(self.plan) - 1)]
        self.i += 1
        return reply


@pytest.mark.tours
@pytest.mark.confirm
def test_the_shop_ceiling_six_bugs_chosen_in_advance(client, shop, monkeypatch, chromium_available):  # noqa: F811
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: Obvious(shop))
    scores = explore_eval.run_eval(shop, "mock", on_progress=lambda *_: None, max_steps=12, app="shop")
    by = {s.charter: s for s in scores}
    found = {k for s in scores for k in s.found}
    assert found == {"double", "confirmation", "phone", "quantity", "email", "total"}, \
        [(s.charter, s.found, s.false_alarms, s.detail) for s in scores]
    assert [a for s in scores for a in s.false_alarms] == []
    assert {"quantity", "total"} <= set(by["quantities"].found)
    assert {"phone", "email"} <= set(by["contact"].found)
    assert {"double", "confirmation"} <= set(by["after-order"].found)  # the flow tour after the first order
    assert not {"phone", "quantity", "email", "total"} & set(by["delivery"].found)  # dates are handled right
    text = explore_eval.report(scores, "obvious", None, "shop")
    assert "on the AddisMart shop practice app" in text and "| The total is wrong for 3 or more items | 1 of 4 runs | 25% |" in text
    from app import models
    from app.database import SessionLocal
    with SessionLocal() as db:
        bugs = db.query(models.Bug).all()
        # one draft per problem: the tour meets the double order and the confirmation error in every
        # charter, and those are sightings of the first drafts
        titles = [b.title for b in bugs]
        assert len(titles) == len(set(titles)) == 6, titles
        assert all(b.reproducibility == "always" for b in bugs), [(b.title, b.confirmation) for b in bugs]
        double = next(b for b in bugs if "twice" in b.title)
        assert len(double.sightings) == 3


def test_the_shop_cli_knows_its_charters():
    with pytest.raises(SystemExit):
        explore_eval.main(["--app", "shop", "--only", "names"])  # a KYC charter
    assert [k for k, _, _ in explore_eval.APPS["shop"].charters] == ["quantities", "contact", "after-order", "delivery"]
