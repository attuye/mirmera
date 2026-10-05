"""The test strategy: a living document per project (optionally focused on a sprint), in the
sections of the Heuristic Test Strategy Model.

Rapid Software Testing prefers a strategy (what we'll test, how, and why, revised as we
learn) to a test plan. Mirmera already knows most of what goes into one: the mission,
the product model, the risks, the heuristics in use, where testing has been. facts()
gathers that per section; draft() turns it into a first version the tester rewrites.
"What we won't test" is never decided here: Mirmera only lists candidates.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session as DBSession

from app import coverage, heuristics, models, openapi, sprints
from app.project_report import HIGH_RISK, tested_seconds
from app.timeutil import utcnow

SECTIONS: list[dict] = [
    {"key": "mission", "title": "Mission", "hint": "What this testing is for, and for whom."},
    {"key": "context", "title": "Context", "hint": "People, time, environments and tools; constraints that shape the testing."},
    {"key": "product", "title": "What we'll test", "hint": "Product elements and changes in scope (structure, function, data, interfaces, platform, operations, time)."},
    {"key": "quality", "title": "Quality criteria", "hint": "Which kinds of quality matter most here, and why."},
    {"key": "risks", "title": "Risks driving the testing", "hint": "The risks that decide where testing goes first."},
    {"key": "approach", "title": "Approach", "hint": "Techniques, heuristics and oracles; how sessions run; tools."},
    {"key": "coverage", "title": "Coverage and gaps so far", "hint": "Where testing has been, and where it hasn't."},
    {"key": "out_of_scope", "title": "What we won't test, and why", "hint": "Decided by the team; Mirmera only suggests candidates."},
    {"key": "logistics", "title": "Logistics", "hint": "Charters, who tests, time, debriefs and reporting."},
]
KEYS = tuple(s["key"] for s in SECTIONS)
MAX_LINES = 12


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _risks(db: DBSession, project: models.Project) -> list[models.Risk]:
    return (db.query(models.Risk).filter(models.Risk.project_id == project.id, models.Risk.status == "accepted").all())


def _sessions(project: models.Project, sprint: models.Sprint | None) -> list[models.Session]:
    rows = [s for c in project.charters for s in c.sessions]
    if sprint:
        start, end = sprints.bounds(sprint)
        rows = [s for s in rows if start <= s.started_at < end]
    return rows


def facts(db: DBSession, project: models.Project, sprint: models.Sprint | None = None) -> dict[str, list[str]]:
    """What Mirmera knows, per section, as short lines. Never invented: every line comes from the data."""
    now = utcnow()
    out: dict[str, list[str]] = {k: [] for k in KEYS}
    risks = sorted(_risks(db, project), key=lambda r: -r.score)
    sessions = _sessions(project, sprint)
    explored = {r.id for s in sessions for r in s.charter.risks}
    sprint_stories = list(sprint.stories) if sprint else []
    sprint_risks = {r.id for st in sprint_stories for r in st.risks}

    # mission
    out["mission"].append(f"Testing mission: {project.testing_mission}" if project.testing_mission
                          else "No testing mission yet (Settings → Testing mission): who is this testing for, and what must it find?")
    if project.description:
        out["mission"].append(f"The product: {project.description}")
    if sprint:
        out["mission"].append(f"Focus: {sprint.name}, {sprint.starts_on:%d %b} to {sprint.ends_on:%d %b %Y}"
                              + (f", goal: {sprint.goal}" if sprint.goal else ""))

    # context
    members = (db.query(models.Membership, models.User).join(models.User)
               .filter(models.Membership.workspace_id == project.org_id).all())
    by_role: dict[str, list[str]] = defaultdict(list)
    for m, u in members:
        by_role[m.role].append(u.name)
    if by_role:
        out["context"].append("People: " + "; ".join(f"{role}s: {', '.join(names)}" for role, names in sorted(by_role.items())))
    if project.app_url:
        out["context"].append(f"App under test: {project.app_url}")
    for env in project.environments or []:
        out["context"].append(f"Environment {env.get('name')}: {env.get('url')}")
    trackers = [t for t in (project.github_repo and f"GitHub {project.github_repo}", project.jira_project_key and f"Jira {project.jira_project_key}") if t]
    if trackers:
        out["context"].append("Bugs are filed to " + " and ".join(trackers))
    if sprint:
        out["context"].append(f"Time: {sprint.name} has {(sprint.ends_on - sprint.starts_on).days + 1} days"
                              + (f", {max((sprint.ends_on - now.date()).days + 1, 0)} left" if sprints.state(sprint) == "current" else ""))

    # product
    model = (db.query(models.ProductModel).filter(models.ProductModel.project_id == project.id)
             .order_by(models.ProductModel.created_at.desc()).first())
    if sprint_stories:
        for st in sprint_stories[:MAX_LINES]:
            out["product"].append(f"Story {st.key + ': ' if st.key else ''}{st.title}{' (done)' if st.closed else ''}")
    if model:
        by_cat: dict[str, list[str]] = defaultdict(list)
        for e in model.elements or []:
            by_cat[str(e.get("category", "other"))].append(str(e.get("name", "")))
        for cat, names in by_cat.items():
            out["product"].append(f"{cat.capitalize()}: {', '.join(names[:8])}{' …' if len(names) > 8 else ''}")
    else:
        out["product"].append("No product model yet (Risks → Model the product): the elements to cover aren't written down.")
    calls = [e for s in sessions for e in s.evidence if e.kind == "api_call"]
    for spec in db.query(models.Artifact).filter(models.Artifact.project_id == project.id, models.Artifact.type == "openapi"):
        try:
            info, ops, _ = openapi.parsed(spec.raw_content)
        except openapi.SpecProblem:
            continue
        tried = sum(1 for o in ops if any((c.data or {}).get("method") == o["method"]
                                          and openapi.path_pattern(o["path"]).search((c.url or "").split("?")[0]) for c in calls))
        out["product"].append(f"API {info['title']} {info['version']}: {_plural(len(ops), 'endpoint')}, {tried} tried"
                              + (" in this sprint" if sprint else ""))

    # quality
    by_quality: dict[str, list[models.Risk]] = defaultdict(list)
    for r in risks:
        by_quality[r.quality_criterion].append(r)
    for q, rs in sorted(by_quality.items(), key=lambda kv: -sum(r.score for r in kv[1])):
        out["quality"].append(f"{q.capitalize()}: {_plural(len(rs), 'risk')}, highest {rs[0].ref} {rs[0].title} (score {rs[0].score})")
    if not risks:
        out["quality"].append("No accepted risks yet (Risks page), so which quality criteria matter most isn't recorded.")

    # risks
    ordered = sorted(risks, key=lambda r: (r.id not in sprint_risks, -r.score)) if sprint else risks
    for r in ordered[:MAX_LINES]:
        status = "explored" if r.id in explored else "not explored" + (" in this sprint" if sprint else "")
        out["risks"].append(f"{r.ref} {r.title} (score {r.score}, {r.quality_criterion}): {status}")
    if len(ordered) > MAX_LINES:
        out["risks"].append(f"And {len(ordered) - MAX_LINES} lower-scoring accepted risks")

    # approach
    charters = [st_c for st in sprint_stories for st_c in st.charters] if sprint else list(project.charters)
    used: dict[str, int] = defaultdict(int)
    for c in charters:
        for key in (c.heuristics or []) + (c.oracles or []):
            used[key] += 1
    if used:
        names = [f"{heuristics.BY_KEY[k].name if k in heuristics.BY_KEY else k} ({n})" for k, n in sorted(used.items(), key=lambda kv: -kv[1])]
        out["approach"].append("Heuristics and oracles in charters: " + ", ".join(names[:10]))
    out["approach"].append(f"Session-based exploratory testing: {_plural(len(sessions), 'session')}"
                           + (" in this sprint" if sprint else " so far") + ", each with notes, bugs and a debrief")
    browser = sum(1 for s in sessions if any(e.kind in ("navigation", "action") for e in s.evidence))
    if browser:
        out["approach"].append(f"Recorded browser or Chrome extension used in {_plural(browser, 'session')}")
    if calls:
        out["approach"].append(f"API exploration: {_plural(len(calls), 'request')} sent from sessions")
    tests = (db.query(models.RegressionTest).join(models.Bug).join(models.Charter)
             .filter(models.Charter.project_id == project.id).count())
    if tests:
        out["approach"].append(f"{_plural(tests, 'regression test')} from bugs, run on the Regression page")

    # coverage
    start, end = sprints.bounds(sprint) if sprint else (None, None)
    cov = coverage.build(db, project, start, end)
    out["coverage"].append(f"{_plural(len(cov['pages']), 'page')} explored in {_plural(cov['recorded_sessions'], 'recorded session')}")
    out["coverage"] += [f"Gap: {g}" for g in cov["gaps"][:6]]
    untested_high = [r for r in risks if r.score >= HIGH_RISK and r.id not in explored]
    out["coverage"] += [f"High risk not explored{' in this sprint' if sprint else ''}: {r.ref} {r.title} (score {r.score})"
                        for r in untested_high[:6]]

    # out of scope: candidates only
    retired = db.query(models.Risk).filter(models.Risk.project_id == project.id, models.Risk.status == "retired").all()
    out["out_of_scope"] += [f"Candidate (retired risk): {r.ref} {r.title}" for r in retired[:5]]
    out["out_of_scope"] += [f"Candidate (low score {r.score}): {r.ref} {r.title}" for r in risks if r.score <= 4][:5]
    for spec in db.query(models.Artifact).filter(models.Artifact.project_id == project.id, models.Artifact.type == "openapi"):
        try:
            dep = [o["key"] for o in openapi.parsed(spec.raw_content)[1] if o["deprecated"]]
        except openapi.SpecProblem:
            continue
        if dep:
            out["out_of_scope"].append(f"Candidate (deprecated endpoints): {', '.join(dep[:5])}")

    # logistics
    by_status: dict[str, int] = defaultdict(int)
    for c in charters:
        by_status[c.status] += 1
    if charters:
        out["logistics"].append("Charters: " + ", ".join(f"{n} {s}" for s, n in sorted(by_status.items())))
    minutes = round(sum(tested_seconds(s, now) for s in sessions) / 60)
    testers = sorted({s.tester_id for s in sessions})
    if sessions:
        out["logistics"].append(f"Testing time: {minutes // 60} h {minutes % 60} min by {', '.join(testers)}")
    undebriefed = [s for s in sessions if s.ended_at and not s.debrief]
    if undebriefed:
        out["logistics"].append(f"{_plural(len(undebriefed), 'session')} without a debrief")
    out["logistics"].append("Reporting: the Report page" + (f" for {sprint.name}" if sprint else "") + ", with Markdown and print exports")
    return out


def draft(f: dict[str, list[str]]) -> dict[str, str]:
    """A first version from the facts: the tester rewrites it. Out of scope stays a question."""
    text = {k: "\n".join(f"- {line}" for line in f[k]) for k in KEYS}
    lead = "Decide with the team what won't be tested and why. Candidates:"
    text["out_of_scope"] = lead + ("\n" + text["out_of_scope"] if f["out_of_scope"] else " none found yet.")
    return text


def fact_lines(f: dict[str, list[str]]) -> list[str]:
    return [line for k in KEYS for line in f[k]]


def markdown(project: models.Project, row: models.TestStrategy) -> str:
    focus = f" · {row.sprint.name}" if row.sprint else ""
    lines = [f"# Test strategy: {project.name}{focus}", "",
             f"Version {row.version}, {row.created_at:%d %b %Y}" + (f", by {row.author.name}" if row.author else ""), ""]
    for s in SECTIONS:
        lines += [f"## {s['title']}", "", (row.sections or {}).get(s["key"], "").strip() or "_(empty)_", ""]
    return "\n".join(lines)
