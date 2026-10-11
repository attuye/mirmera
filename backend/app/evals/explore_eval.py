"""How well does the exploration agent find bugs? Run it on the practice app, whose bugs
are known, and count: which planted bugs it drafted, which it missed, and which drafts
match no planted bug (false alarms). Run it with your own model:

    cd backend
    python -m app.evals.explore_eval --provider local --runs 3
    python -m app.evals.explore_eval --provider local --app shop     # the second practice app

It uses a database and evidence folder of its own (your Mirmera data is not touched),
starts the practice app if nothing answers on --app-url, and writes a report (Markdown)
next to the database. An AI agent doesn't do the same thing twice, so run it a few
times (--runs) and read the rates, not one result.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# The practice app's planted bugs (practice-app/ANSWERS.md), and how to recognise a
# draft of each from its words. A draft matching none is a false alarm.
PLANTED = [
    ("passport", "Passport verification fails (POST /api/verify → 500)",
     lambda t: "/api/verify" in t and "500" in t or ("passport" in t and ("went wrong" in t or "500" in t))),
    ("documents", "My documents shows no documents (page error)",
     lambda t: "reading 'map'" in t or ("my documents" in t or "/documents" in t) and any(w in t for w in ("page error", "empty", "not listed", "missing"))),
    ("geez", "Names in Ge'ez script are rejected",
     lambda t: "ge'ez" in t or "geez" in t or "letters only" in t or bool(re.search("[\u1200-\u137f]", t))),
    ("expired", "Expired documents are accepted",
     lambda t: "expir" in t and any(w in t for w in ("accepted", "verified", "not refused", "not rejected"))),
]

# What the practice app's spec claims, as a story's acceptance criteria.
STORY = """As a new customer, I want to verify my identity online, so that I can open an account.

Acceptance criteria
- Customers can verify with a National ID, a Passport or a Driver's licence; all three are supported equally.
- The full name is entered as written on the document, in Latin or Ge'ez script.
- Expired documents are rejected: a document is expired if its expiry date is before today.
- The phone number is optional.
- The customer must accept the terms before verification.
- After verification, My documents lists every verified document, with the total count.
"""

# One charter per planted bug, worded the way a tester (or Mirmera) would plan them.
CHARTERS = [
    ("documents", "Explore identity verification with each document type (National ID, Passport, Driver's licence) "
                  "to discover whether all three are verified the same way",
     ["Verify with each document type, everything else valid"]),
    ("expiry", "Explore expiry dates with a past date, today and a future date, everything else valid, "
               "to discover whether expired documents are rejected",
     ["An expiry date in the past", "Today's date", "A date next year"]),
    ("names", "Explore full names in Latin and Ge'ez script, with apostrophes and hyphens, "
              "to discover whether names as written on the document are accepted",
     ["A name in Ge'ez script, e.g. አበበ ከበደ", "O'Brien-Smith"]),
    ("my-documents", "Explore My documents after verifying a document to discover whether every verified document is listed",
     ["Verify a National ID first, then open My documents"]),
]

# The second practice app, AddisMart (practice-app/shop.mjs): a shop whose bugs were chosen
# before Mirmera's newer checks were built, to see how far the agent carries beyond the KYC form.
SHOP_PLANTED = [
    ("double", "One double click on Place order creates two orders",
     lambda t: "twice" in t or "two orders" in t or "listed 2 times" in t or "duplicate" in t),
    ("confirmation", "The order confirmation page throws when opened directly or refreshed",
     lambda t: "confirmation" in t and ("page error" in t or "properties of null" in t)),
    ("phone", "Phone is required although the story says it is optional",
     lambda t: "phone" in t and any(w in t for w in ("required", "optional", "left empty"))),
    ("quantity", "A quantity over 10 is accepted",
     lambda t: "quantit" in t and any(w in t for w in ("outside", "over 10", "more than 10", "11", "accepted"))),
    ("email", "An email without a domain is accepted",
     lambda t: "email" in t and any(w in t for w in ("isn't valid", "not valid", "invalid", "name@", "without a domain"))),
    ("total", "The total is wrong for 3 or more items",
     lambda t: "total" in t and any(w in t for w in ("wrong", "incorrect", "should be", "times", "×", "expected"))),
]

SHOP_STORY = """As a shopper, I want to order products online, so that they are delivered to my door.

Acceptance criteria
- A shopper can order any product, with a quantity between 1 and 10.
- The total is the product's price times the quantity.
- The email must be a valid email address.
- The phone number is optional.
- The delivery date is today or later.
- After ordering, a confirmation shows the order and its total.
- My orders lists every placed order once.
"""

SHOP_CHARTERS = [
    ("quantities", "Explore ordering with different quantities to discover whether the quantity limits and the total are right",
     ["Quantities 1 and 10, and just outside them", "Check the total for several quantities"]),
    ("contact", "Explore the order's contact details (email and phone) to discover whether valid details are accepted "
                "and invalid ones refused", ["An order without a phone number", "An email with parts missing"]),
    ("after-order", "Explore what happens after placing an order (the confirmation, My orders, going back, refreshing) "
                    "to discover whether every order is shown once", ["Place an order, then look at My orders"]),
    ("delivery", "Explore delivery dates (yesterday, today, next month) to discover whether past dates are refused",
     ["Yesterday", "Today", "A date next month"]),
]


@dataclass
class PracticeApp:
    name: str
    server: str  # in practice-app/
    port: int
    story_title: str
    story: str
    charters: list
    planted: list


APPS = {
    "kyc": PracticeApp("AddisVerify KYC", "server.mjs", 4100, "Customer verifies identity", STORY, CHARTERS, PLANTED),
    "shop": PracticeApp("AddisMart shop", "shop.mjs", 4200, "Shopper orders products", SHOP_STORY, SHOP_CHARTERS, SHOP_PLANTED),
}


@dataclass
class RunScore:
    charter: str
    status: str
    found: list[str] = field(default_factory=list)  # planted bug ids drafted
    false_alarms: list[str] = field(default_factory=list)  # titles of drafts matching no planted bug
    steps: int = 0  # of the budget used
    actions: int = 0
    unusable: int = 0  # replies the AI got wrong (format)
    refused: int = 0  # steps Mirmera refused (repeats, outside the app, unverified claims)
    checked: int = 0  # answers checked against the rules
    stepped_in: int = 0  # steps Mirmera took itself because the AI was stuck
    tours: int = 0  # Mirmera's flow tours after an accepted submit
    seconds: float = 0.0
    detail: str = ""


def classify(text: str, planted: list | None = None, title: str = "") -> str | None:
    """The planted bug a draft is about: by its title first (a description also mentions the
    form as sent, "Quantity = 1", which shouldn't make an email bug a quantity one), else by all its words."""
    for words in (title.lower(), text.lower()):
        for key, _, match in planted or PLANTED:
            if words and match(words):
                return key
    return None


def score(db, run, planted: list | None = None) -> RunScore:
    from app import models
    bugs = [db.get(models.Bug, b) for b in run.bug_ids or []]
    s = RunScore(charter="", status=run.status, detail=run.detail or "")
    for b in bugs:
        if not b:
            continue
        key = classify(" ".join(filter(None, [b.title, b.description, b.actual, b.expected])), planted, b.title)
        if key and key not in s.found:
            s.found.append(key)
        elif not key:
            s.false_alarms.append(b.title)
    from app.agent.explore import steps_used
    steps = run.steps or []
    s.steps = steps_used(steps)  # of the budget: refused steps, counted below, cost none
    s.actions = sum(1 for x in steps if x.get("kind") not in ("note", "problem", "done", "invalid"))
    s.unusable = sum(1 for x in steps if x.get("kind") == "invalid")
    s.refused = sum(1 for x in steps if x.get("status") in ("refused", "not verified"))
    s.checked = sum(1 for x in steps if x.get("judged"))
    s.stepped_in = sum(1 for x in steps if x.get("by") == "Mirmera" and x.get("kind") not in ("tour", "confirm"))
    s.tours = sum(1 for x in steps if x.get("kind") == "tour")
    if run.finished_at and run.created_at:
        s.seconds = round((run.finished_at - run.created_at).total_seconds(), 1)
    return s


def setup(db, app_url: str, provider: str, checker: str | None, app: str = "kyc"):
    """A project on the practice app, its story, and its charters."""
    from app import models
    pa = APPS[app]
    project = models.Project(org_id="eval", name=f"{pa.name} (eval)", app_url=app_url.rstrip("/"),
                             default_ai_provider=provider,
                             ai_task_providers={"agent": provider, **({"explore_check": checker} if checker else {})})
    db.add(project)
    db.flush()
    story = models.Story(project_id=project.id, key="EVAL-1", title=pa.story_title, description=pa.story)
    db.add(story)
    charters = []
    for key, mission, ideas in pa.charters:
        c = models.Charter(project_id=project.id, mission_statement=mission, test_ideas=ideas, source="eval")
        c.stories = [story]
        db.add(c)
        charters.append((key, c))
    db.commit()
    return project, charters


def run_eval(app_url: str, provider: str, *, checker: str | None = None, runs: int = 1, max_steps: int = 25,
             only: list[str] | None = None, timeout: float = 1800, on_progress=print, app: str = "kyc") -> list[RunScore]:
    """Explores each charter `runs` times, one run at a time, and scores every run."""
    from app import models
    from app.agent import explore as agent
    from app.database import SessionLocal
    from app.routers.explore import start_exploration
    out = []
    with SessionLocal() as db:
        _, charters = setup(db, app_url, provider, checker, app)
        for i in range(runs):
            for key, charter in charters:
                if only and key not in only:
                    continue
                on_progress(f"Run {i + 1}/{runs} · {key}: exploring…")
                run = start_exploration(db, charter, started_by="eval", max_steps=max_steps)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    time.sleep(1)
                    db.expire_all()
                    run = db.get(models.ExplorationRun, run.id)
                    if run.status != "running" and run.id not in agent.ACTIVE:
                        break
                else:
                    run.cancel_requested = True
                    db.commit()
                s = score(db, run, APPS[app].planted)
                s.charter = key
                out.append(s)
                on_progress(f"  {s.status}: found {', '.join(s.found) or 'nothing'}"
                            + (f"; {len(s.false_alarms)} false alarm(s)" if s.false_alarms else "")
                            + f"; {s.steps} steps, {s.unusable} unusable, {s.stepped_in} by Mirmera, {s.seconds}s"
                            + (f"\n  why it stopped: {s.detail}" if s.status == "error" else ""))
    return out


def report(scores: list[RunScore], provider: str, checker: str | None, app: str = "kyc") -> str:
    """The scorecard: per planted bug how often it was found, false alarms, and how the runs went."""
    runs = len({(s.charter, i) for i, s in enumerate(scores)})
    lines = [f"# Exploration agent eval: {provider}" + (f" (checks: {checker})" if checker else ""), "",
             f"{runs} run(s) on the {APPS[app].name} practice app, {sum(s.steps for s in scores)} steps in all.", "",
             "## Planted bugs", "", "| Bug | Found in | Rate |", "| --- | --- | --- |"]
    for key, title, _ in APPS[app].planted:
        hits = sum(1 for s in scores if key in s.found)
        lines.append(f"| {title} | {hits} of {len(scores)} runs | {hits / len(scores):.0%} |" if scores else f"| {title} | – | – |")
    alarms = [a for s in scores for a in s.false_alarms]
    drafts = sum(len(s.found) + len(s.false_alarms) for s in scores)
    lines += ["", "## False alarms", "",
              f"{len(alarms)} of {drafts} drafts matched no planted bug." + (" Review them: some may be real." if alarms else ""), ""]
    lines += [f"- {a}" for a in alarms]
    lines += ["", "## Runs", "", "Mirmera stepped in: steps Mirmera took itself after three steps in a row that taught nothing. "
              "Flow tours: Mirmera's own tours after an accepted submit (open directly, refresh, back, submit twice).", "",
              "| Charter | Status | Found | False alarms | Steps | Actions | Unusable replies | Refused | Mirmera stepped in "
              "| Flow tours | Checked | Seconds |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in scores:
        lines.append(f"| {s.charter} | {s.status} | {', '.join(s.found) or '–'} | {len(s.false_alarms)} | {s.steps} | {s.actions} "
                     f"| {s.unusable} | {s.refused} | {s.stepped_in} | {s.tours} | {s.checked} | {s.seconds} |")
    stopped = [s for s in scores if s.status == "error"]
    if stopped:
        lines += ["", "## Runs that stopped", ""] + [f"- {s.charter}: {s.detail}" for s in stopped]
    return "\n".join(lines) + "\n"


def _answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except Exception:
        return False


def steps_text() -> str:
    """Every step of every run, with the AI's reasons: what to read when a bug was missed."""
    from app import models
    from app.database import SessionLocal
    out = []
    with SessionLocal() as db:
        for run in db.query(models.ExplorationRun).order_by(models.ExplorationRun.created_at).all():
            out += ["", f"## {run.charter.mission_statement}", "", f"{run.status}: {run.detail or ''}", ""]
            for st in run.steps or []:
                who = {"tour": " (Mirmera's flow tour)", "confirm": " (Mirmera's replay)"}.get(st.get("kind"), " (Mirmera stepped in)" if st.get("by") == "Mirmera" else "")
                out.append(f"{st.get('n')}. [{st.get('kind')}] {st.get('action')} -> {st.get('status')}{who}"
                           + (f": {st['detail']}" if st.get("detail") else ""))
                if st.get("judged"):
                    out.append(f"   - checked: {st['judged']}" + (f" ({st['judged_why']})" if st.get("judged_why") else ""))
                if st.get("thought"):
                    out.append(f"   - thought: {st['thought'][:300]}")
    return "# Every step\n" + "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", default="local", help="mock, local, claude or openai (default: local)")
    ap.add_argument("--checker", default=None, help="another provider for checking answers (default: the same)")
    ap.add_argument("--runs", type=int, default=1, help="how many times to explore each charter")
    ap.add_argument("--steps", type=int, default=25, help="step budget per run")
    ap.add_argument("--app", default="kyc", choices=sorted(APPS), help="which practice app: kyc (default) or shop")
    ap.add_argument("--only", nargs="*", choices=sorted({k for a in APPS.values() for k, _, _ in a.charters}),
                    help="just these charters")
    ap.add_argument("--app-url", default=None, help="the practice app (default: localhost:4100 for kyc, 4200 for shop; "
                                                    "started here if nothing answers)")
    ap.add_argument("--ai-only", action="store_true",
                    help="Mirmera never steps in when the AI is stuck: measures the model on its own")
    ap.add_argument("--out", default=None, help="folder for the database, evidence and report (default: a new temp folder)")
    args = ap.parse_args(argv)
    pa = APPS[args.app]
    args.app_url = args.app_url or f"http://localhost:{pa.port}"
    unknown = [k for k in args.only or [] if k not in {c[0] for c in pa.charters}]
    if unknown:
        ap.error(f"{', '.join(unknown)}: not a charter of the {args.app} app")

    folder = Path(args.out or tempfile.mkdtemp(prefix="mirmera-eval-"))
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{(folder / 'eval.db').as_posix()}"  # before the app is imported
    os.environ["EVIDENCE_DIR"] = str(folder / "evidence")
    app_proc = None
    if not _answers(args.app_url):
        server = Path(__file__).resolve().parents[3] / "practice-app" / pa.server
        port = args.app_url.rsplit(":", 1)[-1].strip("/")
        app_proc = subprocess.Popen(["node", str(server)], env={**os.environ, "PORT": port},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _answers(args.app_url):
                break
            time.sleep(0.2)
    try:
        import app.main  # noqa: F401  (creates the database's tables, as the server does)
        if args.ai_only:
            from app.agent import explore as agent
            agent.STEP_IN_AFTER = 10 ** 6
        scores = run_eval(args.app_url, args.provider, checker=args.checker, runs=args.runs, max_steps=args.steps,
                          only=args.only, app=args.app)
        text = report(scores, args.provider, args.checker, args.app)
        (folder / "report.md").write_text(text, encoding="utf-8")
        (folder / "steps.md").write_text(steps_text(), encoding="utf-8")
        print("\n" + text + f"\nReport (report.md), every step (steps.md), database and recordings: {folder}")
    finally:
        if app_proc:
            app_proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
