"""A project's test report, told as three stories (Bach's "testing story"):

1. The product: what was found, where the risks stand, what is fixed.
2. The testing: which sessions ran, by whom, for how long, on what.
3. How good the testing was: obstacles, gaps, untested risks, open questions.

The report is built once as a list of blocks (headings, paragraphs, tables,
lists, quotes) and rendered three ways from the same blocks: the Report page,
Markdown, and a printable HTML page. Everything comes from what was recorded:
nothing is written by the AI, so the report never claims more than happened.
"""
import html
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session as DBSession

from app import models
from app.regression.suite import latest_tests, meaning
from app.timeutil import utcnow

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PHASES = {"on_charter": "On charter", "opportunity": "Opportunity", "bug_investigation": "Bug investigation", "setup": "Setup"}
CHARTER_STATUS = {"fulfilled": "Fulfilled", "partially_fulfilled": "Partly fulfilled", "not_fulfilled": "Not fulfilled"}
RETEST = {"running": "Replaying", "fixed": "Fix verified", "still_fails": "Still fails",
          "could_not_check": "Couldn't check", "no_test": "No test"}
HIGH_RISK = 12  # impact x likelihood at or above this is a high risk


# ---------- blocks ----------

def h(text: str, level: int = 2) -> dict:
    return {"type": "heading", "level": level, "text": text}


def p(text: str) -> dict:
    return {"type": "paragraph", "text": text}


def table(headers: list[str], rows: list[list]) -> dict:
    """A cell is text, or {"text", "href"} for a link."""
    return {"type": "table", "headers": headers, "rows": rows}


def bullets(items: list[str]) -> dict:
    return {"type": "bullets", "items": items}


def quote(text: str, by: str) -> dict:
    return {"type": "quote", "text": text, "by": by}


def link(text: str, href: str | None) -> dict | str:
    return {"text": text, "href": href} if href else text


# ---------- helpers ----------

def period_bounds(since: date | None, until: date | None) -> tuple[datetime | None, datetime | None]:
    """Whole days, UTC: until is included."""
    start = datetime.combine(since, time.min) if since else None
    end = datetime.combine(until + timedelta(days=1), time.min) if until else None
    return start, end


def _in(ts: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    return ts is not None and (start is None or ts >= start) and (end is None or ts < end)


def tested_seconds(s: models.Session, now: datetime) -> float:
    """Wall time minus pauses; a session still running counts up to now (or to its pause)."""
    stop = s.ended_at or s.paused_at or now
    return max((stop - s.started_at).total_seconds() - (s.total_paused_seconds or 0), 0.0)


def hours(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _day(ts: datetime) -> str:
    return f"{ts:%Y-%m-%d}"


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _issue(bug: models.Bug) -> list[dict | str]:
    out = []
    for f in bug.filings:
        state = " (closed)" if f.issue_closed else ""
        out.append(link(f"{f.issue_key}{state}", f.issue_url))
    return out


# ---------- the report ----------

def build(db: DBSession, project: models.Project, since: date | None = None, until: date | None = None) -> dict:
    now = utcnow()
    start, end = period_bounds(since, until)
    charters = db.query(models.Charter).filter(models.Charter.project_id == project.id).all()
    charter_by_id = {c.id: c for c in charters}
    sessions = [s for c in charters for s in c.sessions if _in(s.started_at, start, end)]
    sessions.sort(key=lambda s: s.started_at)
    session_ids = {s.id for s in sessions}
    all_bugs = (db.query(models.Bug).filter(models.Bug.charter_id.in_(list(charter_by_id))).all() if charters else [])
    bugs = sorted([b for b in all_bugs if _in(b.created_at, start, end)],
                  key=lambda b: (SEVERITY_ORDER.get(b.severity, 3), b.created_at))
    reviewed = [b for b in bugs if b.kind == "bug"]
    questions = [b for b in bugs if b.kind == "question"]
    unreviewed = [b for b in bugs if not b.kind]
    tests = {b.id: t for b, t in latest_tests(db, project.id)}
    retests = (db.query(models.Retest).filter(models.Retest.project_id == project.id)
               .order_by(models.Retest.created_at).all())
    retests = [r for r in retests if _in(r.created_at, start, end)]
    last_retest = {r.bug_id: r for r in retests}
    suite_runs = (db.query(models.RegressionSuiteRun).filter(models.RegressionSuiteRun.project_id == project.id,
                                                             models.RegressionSuiteRun.status == "done")
                  .order_by(models.RegressionSuiteRun.created_at).all())
    suite_run = next((r for r in reversed(suite_runs) if _in(r.created_at, start, end)), None)
    risks = sorted([r for r in project.risks if r.status == "accepted"], key=lambda r: (-r.score, r.ref))

    total_seconds = sum(tested_seconds(s, now) for s in sessions)
    testers = Counter()
    for s in sessions:
        testers[s.tester_id] += tested_seconds(s, now)
    filed = [b for b in bugs if b.filings]
    closed = [b for b in filed if any(f.issue_closed for f in b.filings)]
    severity = Counter(b.severity for b in reviewed)

    summary = {
        "sessions": len(sessions), "tested_seconds": round(total_seconds), "testers": len(testers),
        "bugs": len(reviewed), "high": severity.get("high", 0), "medium": severity.get("medium", 0), "low": severity.get("low", 0),
        "questions": len(questions), "unreviewed": len(unreviewed), "filed": len(filed), "closed": len(closed),
        "regression_tests": len(tests), "fix_verified": sum(1 for r in retests if r.outcome == "fixed"),
        "still_fails": sum(1 for r in retests if r.outcome == "still_fails"),
    }
    period = (f"{since or 'the start'} to {until or _day(now)}" if since or until else f"all time, to {_day(now)}")
    blocks: list[dict] = []

    # Overview
    blocks.append(p(
        f"{_plural(len(sessions), 'session')} of exploratory testing ({hours(total_seconds)}) by "
        f"{_plural(len(testers), 'tester')}, {period}. Found {_plural(len(reviewed), 'bug')}"
        f" ({severity.get('high', 0)} high, {severity.get('medium', 0)} medium, {severity.get('low', 0)} low)"
        f" and {_plural(len(questions), 'question')}; {len(filed)} filed, {len(closed)} closed."
        + (f" {_plural(len(unreviewed), 'report')} still to review." if unreviewed else "")))
    if project.testing_mission:
        blocks.append(p(f"Testing mission: {project.testing_mission}"))

    # ---------- 1. the product ----------
    blocks.append(h("1. The product: what we found"))
    if reviewed or questions:
        rows = []
        for b in reviewed + questions:
            test = tests.get(b.id)
            last = (test.replay or {}).get("outcome") if test else None
            rt = last_retest.get(b.id)
            rows.append([
                b.title, b.severity if b.kind == "bug" else "question", b.reproducibility or "",
                (_issue(b) or ["not filed"]),
                (meaning(last, test.known_failing) if last else ("not replayed" if test else "no test")),
                RETEST.get(rt.outcome, rt.outcome) if rt else "",
            ])
        blocks.append(table(["Bug", "Severity", "Reproduces", "Issue", "Regression test", "Retest"], rows))
    else:
        blocks.append(p("No bugs or questions were reviewed in this period."))
    if unreviewed:
        blocks.append(p(f"Not reviewed yet (not counted above): {', '.join(b.title for b in unreviewed)}."))

    blocks.append(h("Risks", 3))
    if risks:
        sessions_on: dict[str, list[models.Session]] = defaultdict(list)
        bugs_on: Counter = Counter()
        for s in sessions:
            for r in s.charter.risks:
                sessions_on[r.id].append(s)
        for b in reviewed:
            if b.session_id in session_ids:
                for r in charter_by_id[b.charter_id].risks:
                    bugs_on[r.id] += 1
        rows = []
        for r in risks:
            ran = sessions_on.get(r.id, [])
            rows.append([f"{r.ref} {r.title}", f"{r.score} ({r.impact}x{r.likelihood})", r.quality_criterion,
                         _plural(len(ran), "session") if ran else "not tested", str(bugs_on.get(r.id, 0))])
        blocks.append(table(["Risk", "Score", "Quality", "Tested", "Bugs"], rows))
    else:
        blocks.append(p("No accepted risks yet. Review the AI's risks on the Risks page to track them here."))

    blocks.append(h("Regression", 3))
    if suite_run:
        outcomes = Counter(meaning(x["outcome"], x["known_failing"]) if x["outcome"] != "error" else "Could not run"
                           for x in suite_run.results)
        blocks.append(p(f"Last full run {_day(suite_run.created_at)} on "
                        f"{suite_run.base_url or 'each test’s recorded address'}: "
                        + "; ".join(f"{n} {m.lower()}" for m, n in outcomes.most_common()) + "."))
    elif tests:
        blocks.append(p(f"{_plural(len(tests), 'regression test')}, not all run together in this period "
                        "(Run all on the Regression page)."))
    else:
        blocks.append(p("No regression tests yet."))
    if retests:
        blocks.append(table(["Issue", "Bug", "Retest", "When"], [
            [link(r.issue_key, r.issue_url), _bug_title(all_bugs, r.bug_id), RETEST.get(r.outcome, r.outcome), _day(r.created_at)]
            for r in retests]))

    # ---------- 2. the testing ----------
    blocks.append(h("2. The testing: how we tested"))
    if sessions:
        rows = []
        for s in sessions:
            d = s.debrief
            rows.append([s.charter.mission_statement, s.tester_id, _day(s.started_at),
                         hours(tested_seconds(s, now)) + ("" if s.ended_at else " (running)"),
                         CHARTER_STATUS.get(d.charter_status, "") if d else "no debrief",
                         str(sum(1 for b in s.bugs if b.kind))])
        blocks.append(table(["Charter", "Tester", "Date", "Time", "Outcome", "Bugs"], rows))
        blocks.append(p("By tester: " + "; ".join(f"{t} {hours(sec)}" for t, sec in testers.most_common()) + "."))
        split = _time_split(sessions, now)
        if split:
            blocks.append(p("Where the time went (from session notes, debriefed sessions): "
                            + ", ".join(f"{PHASES[k].lower()} {round(v * 100)}%" for k, v in split.items()) + "."))
        coverage = [f"{s.charter.mission_statement}: {s.debrief.coverage_summary}" for s in sessions
                    if s.debrief and s.debrief.coverage_summary]
        if coverage:
            blocks += [h("What the sessions covered", 3), bullets(coverage)]
    else:
        blocks.append(p("No sessions in this period."))

    # ---------- 3. how good the testing was ----------
    blocks.append(h("3. How good the testing was"))
    gaps: list[str] = []
    untested = [r for r in risks if r.score >= HIGH_RISK and not any(r in s.charter.risks for s in sessions)]
    gaps += [f"High risk not tested: {r.ref} {r.title} (score {r.score})" for r in untested]
    for s in sessions:
        d = s.debrief
        if not s.ended_at:
            gaps.append(f"Session still running: {s.charter.mission_statement} ({s.tester_id})")
        elif not d:
            gaps.append(f"No debrief yet: {s.charter.mission_statement} ({s.tester_id}, {_day(s.started_at)})")
        elif d.charter_status in ("partially_fulfilled", "not_fulfilled"):
            why = f": {d.charter_status_rationale}" if d.charter_status_rationale else ""
            gaps.append(f"Charter {CHARTER_STATUS[d.charter_status].lower()}: {s.charter.mission_statement}{why}")
    if unreviewed:
        gaps.append(f"{_plural(len(unreviewed), 'bug report')} not reviewed: severity and oracle unknown")
    no_test = [b for b in reviewed if b.id not in tests]
    if no_test:
        gaps.append(f"{_plural(len(no_test), 'bug')} without a regression test: a fix can't be checked automatically")
    blocks += [h("Gaps", 3), bullets(gaps) if gaps else p("No gaps recorded.")]

    obstacles = [f"{o} ({s.tester_id}, {s.charter.mission_statement})" for s in sessions if s.debrief
                 for o in (s.debrief.obstacles or [])]
    if obstacles:
        blocks += [h("Obstacles", 3), bullets(obstacles)]
    open_q = [q for s in sessions if s.debrief for q in (s.debrief.open_questions or [])]
    open_q += [f"{q} (bug: {b.title})" for b in bugs for q in (b.open_questions or [])]
    if open_q:
        blocks += [h("Open questions", 3), bullets(open_q)]
    run_missions = {c.mission_statement.strip().lower() for c in charters if c.sessions}
    outlook = [o.get("charter", "") + (f" ({o['reason']})" if o.get("reason") else "")
               for s in sessions if s.debrief for o in (s.debrief.outlook or [])
               if o.get("charter") and o["charter"].strip().lower() not in run_missions]
    if outlook:
        blocks += [h("Testing still to do", 3), bullets(outlook)]
    feelings = [quote(s.debrief.tester_feelings, f"{s.tester_id}, {s.charter.mission_statement}")
                for s in sessions if s.debrief and s.debrief.tester_feelings]
    if feelings:
        blocks += [h("How the testers felt", 3), *feelings]

    return {"project_id": project.id, "project_name": project.name, "since": since, "until": until,
            "period": period, "generated_at": now, "summary": summary, "blocks": blocks}


def _bug_title(bugs: list[models.Bug], bug_id: str) -> str:
    return next((b.title for b in bugs if b.id == bug_id), "")


def _time_split(sessions: list[models.Session], now: datetime) -> dict[str, float]:
    """Each debriefed session's split, weighted by its length."""
    totals: Counter = Counter()
    for s in sessions:
        split = (s.debrief.time_breakdown or {}) if s.debrief else {}
        weight = tested_seconds(s, now)
        for k, v in split.items():
            if k in PHASES:
                totals[k] += v * weight
    whole = sum(totals.values())
    return {k: totals[k] / whole for k in PHASES if totals[k]} if whole else {}


# ---------- renderings ----------

def _cell_md(cell) -> str:
    if isinstance(cell, list):
        return ", ".join(_cell_md(c) for c in cell)
    if isinstance(cell, dict):
        return f"[{_esc_md(cell['text'])}]({cell['href']})"
    return _esc_md(str(cell))


def _esc_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(report: dict) -> str:
    out = [f"# Test report: {report['project_name']}", "",
           f"Period: {report['period']}. Generated {report['generated_at']:%Y-%m-%d %H:%M} UTC by Mirmera.", ""]
    for b in report["blocks"]:
        t = b["type"]
        if t == "heading":
            out += ["#" * b["level"] + " " + b["text"], ""]
        elif t == "paragraph":
            out += [b["text"], ""]
        elif t == "bullets":
            out += [f"- {i}" for i in b["items"]] + [""]
        elif t == "quote":
            out += [f"> {b['text']}", f"> — {b['by']}", ""]
        elif t == "table":
            out.append("| " + " | ".join(b["headers"]) + " |")
            out.append("|" + "---|" * len(b["headers"]))
            out += ["| " + " | ".join(_cell_md(c) for c in row) + " |" for row in b["rows"]]
            out.append("")
    return "\n".join(out)


def _cell_html(cell) -> str:
    if isinstance(cell, list):
        return ", ".join(_cell_html(c) for c in cell)
    if isinstance(cell, dict):
        return f'<a href="{html.escape(cell["href"])}">{html.escape(cell["text"])}</a>'
    return html.escape(str(cell))


STYLE = """
body { font: 14px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; color: #1b1d21; max-width: 960px; margin: 32px auto; padding: 0 24px; }
h1 { font-size: 24px; margin: 0 0 4px; } h2 { font-size: 18px; margin: 28px 0 8px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
h3 { font-size: 15px; margin: 18px 0 6px; } .meta { color: #666; font-size: 12.5px; margin-bottom: 18px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 6px 0 12px; }
th, td { text-align: left; padding: 5px 7px; border-bottom: 1px solid #e3e3e3; vertical-align: top; }
th { color: #555; font-weight: 600; } blockquote { margin: 8px 0; padding: 4px 12px; border-left: 3px solid #bbb; color: #333; }
blockquote footer { color: #777; font-size: 12px; } a { color: #1a5fb4; }
@media print { body { margin: 0; } a { color: inherit; text-decoration: none; } h2 { break-after: avoid; } tr { break-inside: avoid; } }
"""


def to_html(report: dict) -> str:
    out = [f"<h1>Test report: {html.escape(report['project_name'])}</h1>",
           f'<div class="meta">Period: {html.escape(report["period"])}. '
           f"Generated {report['generated_at']:%Y-%m-%d %H:%M} UTC by Mirmera.</div>"]
    for b in report["blocks"]:
        t = b["type"]
        if t == "heading":
            out.append(f"<h{b['level']}>{html.escape(b['text'])}</h{b['level']}>")
        elif t == "paragraph":
            out.append(f"<p>{html.escape(b['text'])}</p>")
        elif t == "bullets":
            out.append("<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in b["items"]) + "</ul>")
        elif t == "quote":
            out.append(f"<blockquote>{html.escape(b['text'])}<footer>— {html.escape(b['by'])}</footer></blockquote>")
        elif t == "table":
            head = "".join(f"<th>{html.escape(x)}</th>" for x in b["headers"])
            rows = "".join("<tr>" + "".join(f"<td>{_cell_html(c)}</td>" for c in row) + "</tr>" for row in b["rows"])
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
    title = html.escape(f"Test report: {report['project_name']}")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><style>{STYLE}</style></head>'
            f"<body>{''.join(out)}</body></html>")
