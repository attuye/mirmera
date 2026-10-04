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
from app.config import settings
from app.regression.suite import latest_tests, meaning
from app.timeutil import utcnow

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PHASES = {"on_charter": "On charter", "opportunity": "Opportunity", "bug_investigation": "Bug investigation", "setup": "Setup"}
CHARTER_STATUS = {"fulfilled": "Fulfilled", "partially_fulfilled": "Partly fulfilled", "not_fulfilled": "Not fulfilled"}
RETEST = {"running": "Replaying", "fixed": "Fix verified", "still_fails": "Still fails",
          "could_not_check": "Couldn't check", "no_test": "No test"}
HIGH_RISK = 12  # impact x likelihood at or above this is a high risk
TOP = 5  # rows in the summary's top bugs
GROUP_OVER = 2  # more running or undebriefed sessions than this: one gap line for them all


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
    def fixed(b: models.Bug) -> bool:
        """Its last retest verified the fix, or its regression test now passes."""
        rt, test = last_retest.get(b.id), tests.get(b.id)
        if rt and rt.outcome in ("fixed", "still_fails"):
            return rt.outcome == "fixed"
        return bool(test and (test.replay or {}).get("outcome") == "passes")

    # ---------- 1. the product ----------
    found_rows = []
    for b in reviewed + questions:
        test = tests.get(b.id)
        last = (test.replay or {}).get("outcome") if test else None
        rt = last_retest.get(b.id)
        found_rows.append([
            b.title, b.severity if b.kind == "bug" else "question", b.reproducibility or "",
            (_issue(b) or ["not filed"]),
            (meaning(last, test.known_failing) if last else ("not replayed" if test else "no test")),
            RETEST.get(rt.outcome, rt.outcome) if rt else "",
        ])
    found_headers = ["Bug", "Severity", "Reproduces", "Issue", "Regression test", "Retest"]
    product = part(None, [table(found_headers, found_rows)] if found_rows
                   else [p("No bugs or questions were reviewed in this period.")])
    if unreviewed:
        product["blocks"].append(p(f"Not reviewed yet (not counted above): {', '.join(b.title for b in unreviewed)}."))

    sessions_on: dict[str, list[models.Session]] = defaultdict(list)
    bugs_on: Counter = Counter()
    for s in sessions:
        for r in s.charter.risks:
            sessions_on[r.id].append(s)
    for b in reviewed:
        if b.session_id in session_ids:
            for r in charter_by_id[b.charter_id].risks:
                bugs_on[r.id] += 1
    untested = [r for r in risks if not sessions_on.get(r.id)]
    untested_high = [r for r in untested if r.score >= HIGH_RISK]
    risk_part = part("Risks", [table(["Risk", "Score", "Quality", "Tested", "Bugs"], [
        [f"{r.ref} {r.title}", f"{r.score} ({r.impact}x{r.likelihood})", r.quality_criterion,
         _plural(len(sessions_on[r.id]), "session") if sessions_on.get(r.id) else "not tested", str(bugs_on.get(r.id, 0))]
        for r in risks])] if risks else [p("No accepted risks yet. Review the AI's risks on the Risks page to track them here.")],
        count=len(risks))

    regression_blocks = []
    if suite_run:
        outcomes = Counter(meaning(x["outcome"], x["known_failing"]) if x["outcome"] != "error" else "Could not run"
                           for x in suite_run.results)
        regression_blocks.append(p(f"Last full run {_day(suite_run.created_at)} on "
                                   f"{suite_run.base_url or 'each test’s recorded address'}: "
                                   + "; ".join(f"{n} {m.lower()}" for m, n in outcomes.most_common()) + "."))
    elif tests:
        regression_blocks.append(p(f"{_plural(len(tests), 'regression test')}, not all run together in this period "
                                   "(Run all on the Regression page)."))
    else:
        regression_blocks.append(p("No regression tests yet."))
    if retests:
        regression_blocks.append(table(["Issue", "Bug", "Retest", "When"], [
            [link(r.issue_key, r.issue_url), _bug_title(all_bugs, r.bug_id), RETEST.get(r.outcome, r.outcome), _day(r.created_at)]
            for r in retests]))
    product_counts = [_plural(len(reviewed), "bug"), _plural(len(questions), "question"), _plural(len(risks), "risk")]
    if suite_run:
        product_counts.append(f"last regression run {_day(suite_run.created_at)}")

    # ---------- 2. the testing ----------
    split = _time_split(sessions, now)
    testing_parts = []
    if sessions:
        rows = []
        for s in sessions:
            d = s.debrief
            rows.append([s.charter.mission_statement, s.tester_id, _day(s.started_at),
                         hours(tested_seconds(s, now)) + ("" if s.ended_at else " (running)"),
                         CHARTER_STATUS.get(d.charter_status, "") if d else "no debrief",
                         str(sum(1 for b in s.bugs if b.kind))])
        blocks = [table(["Charter", "Tester", "Date", "Time", "Outcome", "Bugs"], rows),
                  p("By tester: " + "; ".join(f"{t} {hours(sec)}" for t, sec in testers.most_common()) + ".")]
        if split:
            blocks.append(p("Where the time went (from session notes, debriefed sessions): "
                            + ", ".join(f"{PHASES[k].lower()} {round(v * 100)}%" for k, v in split.items()) + "."))
        testing_parts.append(part(None, blocks))
        coverage = [f"{s.charter.mission_statement}: {s.debrief.coverage_summary}" for s in sessions
                    if s.debrief and s.debrief.coverage_summary]
        if coverage:
            testing_parts.append(part("What the sessions covered", [bullets(coverage)], count=len(coverage), open=False))
    else:
        testing_parts.append(part(None, [p("No sessions in this period.")]))

    # ---------- 3. how good the testing was ----------
    gaps: list[dict] = []
    gaps += [action(f"High risk not tested: {r.ref} {r.title} (score {r.score})", "Make a charter", "/risks") for r in untested_high]
    running = [s for s in sessions if not s.ended_at]
    undebriefed = [s for s in sessions if s.ended_at and not s.debrief]
    if len(running) > GROUP_OVER:  # one line, not one per session
        gaps.append(action(f"{len(running)} sessions still running: {_names([s.charter.mission_statement for s in running])}",
                           "Dashboard", "/dashboard"))
    if len(undebriefed) > GROUP_OVER:
        gaps.append(action(f"{len(undebriefed)} sessions without a debrief: {_names([s.charter.mission_statement for s in undebriefed])}",
                           "Debriefs", "/debriefs"))
    for s in sessions:
        d = s.debrief
        if not s.ended_at:
            if len(running) <= GROUP_OVER:
                gaps.append(action(f"Session still running: {s.charter.mission_statement} ({s.tester_id})", "Open session", f"/sessions/{s.id}"))
        elif not d:
            if len(undebriefed) <= GROUP_OVER:
                gaps.append(action(f"No debrief yet: {s.charter.mission_statement} ({s.tester_id}, {_day(s.started_at)})",
                                   "Write debrief", f"/sessions/{s.id}/debrief"))
        elif d.charter_status in ("partially_fulfilled", "not_fulfilled"):
            why = f": {d.charter_status_rationale}" if d.charter_status_rationale else ""
            gaps.append(action(f"Charter {CHARTER_STATUS[d.charter_status].lower()}: {s.charter.mission_statement}{why}",
                               "Open debrief", f"/sessions/{s.id}/debrief"))
    if unreviewed:
        gaps.append(action(f"{_plural(len(unreviewed), 'bug report')} not reviewed: severity and oracle unknown", "Review", "/bugs"))
    no_test = [b for b in reviewed if b.id not in tests]
    if no_test:
        gaps.append(action(f"{_plural(len(no_test), 'bug')} without a regression test: a fix can't be checked automatically",
                           "Generate tests", "/bugs"))
    quality = [part("Gaps", [actions(gaps)] if gaps else [p("No gaps recorded.")], count=len(gaps))]
    obstacles = [f"{o} ({s.tester_id}, {s.charter.mission_statement})" for s in sessions if s.debrief
                 for o in (s.debrief.obstacles or [])]
    if obstacles:
        quality.append(part("Obstacles", [bullets(obstacles)], count=len(obstacles), open=False))
    open_q = [q for s in sessions if s.debrief for q in (s.debrief.open_questions or [])]
    open_q += [f"{q} (bug: {b.title})" for b in bugs for q in (b.open_questions or [])]
    if open_q:
        quality.append(part("Open questions", [bullets(open_q)], count=len(open_q), open=False))
    run_missions = {c.mission_statement.strip().lower() for c in charters if c.sessions}
    outlook = [o.get("charter", "") + (f" ({o['reason']})" if o.get("reason") else "")
               for s in sessions if s.debrief for o in (s.debrief.outlook or [])
               if o.get("charter") and o["charter"].strip().lower() not in run_missions]
    if outlook:
        quality.append(part("Testing still to do", [bullets(outlook)], count=len(outlook), open=False))
    feelings = [quote(s.debrief.tester_feelings, f"{s.tester_id}, {s.charter.mission_statement}")
                for s in sessions if s.debrief and s.debrief.tester_feelings]
    if feelings:
        quality.append(part("How the testers felt", feelings, count=len(feelings), open=False))

    sections = [
        {"key": "product", "title": "1. The product: what we found", "counts": " · ".join(product_counts),
         "open": False, "parts": [product, risk_part, part("Regression", regression_blocks)]},
        {"key": "testing", "title": "2. The testing: how we tested",
         "counts": f"{_plural(len(sessions), 'session')} · {hours(total_seconds)} · {_plural(len(testers), 'tester')}",
         "open": False, "parts": testing_parts},
        {"key": "quality", "title": "3. How good the testing was",
         "counts": " · ".join(f"{q['count']} {q['title'].lower()}" for q in quality if q["count"] and q["title"] in ("Gaps", "Obstacles", "Open questions")) or "no gaps",
         "open": True, "parts": quality},
    ]

    # ---------- at a glance: what matters most, with where to act ----------
    glance: list[dict] = []
    high_open = [b for b in reviewed if b.severity == "high" and not fixed(b)]
    if high_open:
        glance.append(line("danger", f"{_plural(len(high_open), 'high bug')} still open", _names(high_open), "Bugs", "/bugs"))
    if untested_high:
        glance.append(line("danger", f"{_plural(len(untested_high), 'high risk')} not tested",
                           _names([f"{r.ref} {r.title} (score {r.score})" for r in untested_high]), "Make a charter", "/risks"))
    held = [r for r in retests if r.outcome == "still_fails"]
    if held:
        title = (f"1 fix didn't hold: {held[0].issue_key} was closed, but the bug still reproduces" if len(held) == 1
                 else f"{len(held)} fixes didn't hold: closed, but the bugs still reproduce")
        glance.append(line("warning", title, _names([_bug_title(all_bugs, r.bug_id) for r in held]), "See retests", "/regression"))
    verified = [r for r in retests if r.outcome == "fixed"]
    if verified:
        glance.append(line("ok", f"{_plural(len(verified), 'fix', 'fixes')} verified after the issue closed",
                           _names([f"{r.issue_key} {_bug_title(all_bugs, r.bug_id)}" for r in verified])))
    unfinished = [s for s in sessions if s.debrief and s.debrief.charter_status in ("partially_fulfilled", "not_fulfilled")]
    no_debrief = [s for s in sessions if s.ended_at and not s.debrief]
    if unfinished or no_debrief:
        how = "partly fulfilled" if all(x.debrief.charter_status == "partially_fulfilled" for x in unfinished) else "not fully done"
        bits = ([_plural(len(unfinished), "charter") + f" {how}"] if unfinished else []) + \
               ([_plural(len(no_debrief), "session") + " without a debrief"] if no_debrief else [])
        only = (unfinished + no_debrief)[0] if len(unfinished + no_debrief) == 1 else None
        glance.append(line("neutral", ", ".join(bits), _names([s.charter.mission_statement for s in unfinished + no_debrief]),
                           "Open", f"/sessions/{only.id}/debrief" if only else "/debriefs"))
    if unreviewed:
        glance.append(line("question", f"{_plural(len(unreviewed), 'report')} waiting for review",
                           "Severity and oracle unknown until reviewed", "Review", "/bugs"))
    if not glance:
        glance.append(line("ok", "Nothing needs attention", "No open high bugs, untested high risks or gaps in this period")
                      if sessions else line("neutral", "No sessions in this period", "Start one from the Dashboard", "Dashboard", "/dashboard"))

    charts = {
        "severity": {k: severity.get(k, 0) for k in ("high", "medium", "low")},
        "risks": {"tested": len(risks) - len(untested), "untested": len(untested), "untested_high": len(untested_high)},
        "time": {"seconds": round(total_seconds), "split": split},
    }
    top_bugs = {"headers": found_headers[:5], "rows": [r[:5] for r in found_rows[:TOP]], "total": len(found_rows),
                "bugs": len(reviewed), "questions": len(questions)}
    blocks = [b for sec in sections for b in [h(sec["title"])] + [x for pt in sec["parts"]
              for x in ([h(pt["title"], 3)] if pt["title"] else []) + pt["blocks"]]]
    if project.testing_mission:
        blocks.insert(0, p(f"Testing mission: {project.testing_mission}"))

    return {"project_id": project.id, "project_name": project.name, "since": since, "until": until,
            "period": period, "generated_at": now, "summary": summary, "glance": glance, "charts": charts,
            "top_bugs": top_bugs, "sections": sections, "blocks": blocks}


def facts(report: dict) -> list[str]:
    """The report's own lines, for the AI summary: nothing else reaches the model."""
    out = [f"Period: {report['period']}", f"Totals: {_totals(report)}"]
    out += [f"At a glance: {g['title']}" + (f": {g['detail']}" if g["detail"] else "") for g in report["glance"]]
    out += [line[2:] for line in _charts_text(report) if line]
    for sec in report["sections"]:
        for pt in sec["parts"]:
            for b in pt["blocks"]:
                if b["type"] == "actions":
                    out += [f"Gap: {a['text']}" for a in b["items"]]
    tb = report["top_bugs"]
    out += [f"Bug: {r[0]} ({r[1]}, regression test: {r[4]})" for r in tb["rows"]]
    return out


def part(title: str | None, blocks: list[dict], count: int | None = None, open: bool = True) -> dict:
    """A foldable piece of a section; title None: shown without a header."""
    return {"title": title, "count": count, "open": open, "blocks": blocks}


def action(text: str, label: str, to: str) -> dict:
    """A gap with where to act on it: to is a page of the project ("/risks", "/sessions/<id>")."""
    return {"text": text, "label": label, "to": to}


def actions(items: list[dict]) -> dict:
    return {"type": "actions", "items": items}


def line(tone: str, title: str, detail: str, label: str | None = None, to: str | None = None) -> dict:
    """An "at a glance" line. tone: danger | warning | ok | neutral | question."""
    return {"tone": tone, "title": title, "detail": detail, "label": label, "to": to}


def _names(items: list, limit: int = 3) -> str:
    names = [i if isinstance(i, str) else i.title for i in items]
    more = f" and {len(names) - limit} more" if len(names) > limit else ""
    return " · ".join(names[:limit]) + more


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

def _app_link(report: dict, to: str | None) -> str | None:
    """A link into Mirmera, only when PUBLIC_APP_URL says where testers open it."""
    if not to or not settings.public_app_url:
        return None
    return f"{settings.public_app_url.rstrip('/')}/projects/{report['project_id']}{to}"


def _summary_label(summary: dict) -> str:
    who = " · ".join(x for x in (summary.get("ai_provider"), summary.get("ai_model")) if x)
    return ("Drafted by AI" + (f" ({who})" if who else "") + (", edited by the tester" if summary.get("edited") else ", kept by the tester"))


def _cell_md(cell) -> str:
    if isinstance(cell, list):
        return ", ".join(_cell_md(c) for c in cell)
    if isinstance(cell, dict):
        return f"[{_esc_md(cell['text'])}]({cell['href']})"
    return _esc_md(str(cell))


def _esc_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _totals(report: dict) -> str:
    s = report["summary"]
    return (f"{_plural(s['sessions'], 'session')} ({hours(s['tested_seconds'])}, {_plural(s['testers'], 'tester')}) · "
            f"{_plural(s['bugs'], 'bug')} ({s['high']} high, {s['medium']} medium, {s['low']} low) · "
            f"{_plural(s['questions'], 'question')} · {s['filed']} filed, {s['closed']} closed")


def _blocks_md(report: dict, blocks: list[dict]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        t = b["type"]
        if t == "heading":
            out += ["#" * b["level"] + " " + b["text"], ""]
        elif t == "paragraph":
            out += [b["text"], ""]
        elif t == "bullets":
            out += [f"- {i}" for i in b["items"]] + [""]
        elif t == "actions":
            for a in b["items"]:
                url = _app_link(report, a["to"])
                out.append(f"- {a['text']}" + (f" ([{a['label']}]({url}))" if url else ""))
            out.append("")
        elif t == "quote":
            out += [f"> {b['text']}", f"> — {b['by']}", ""]
        elif t == "table":
            out.append("| " + " | ".join(b["headers"]) + " |")
            out.append("|" + "---|" * len(b["headers"]))
            out += ["| " + " | ".join(_cell_md(c) for c in row) + " |" for row in b["rows"]]
            out.append("")
    return out


def _glance_md(report: dict) -> list[str]:
    out = ["## At a glance", ""]
    for g in report["glance"]:
        url = _app_link(report, g["to"])
        out.append(f"- **{g['title']}**" + (f": {g['detail']}" if g["detail"] else "") + (f" ([{g['label']}]({url}))" if url else ""))
    return out + [""]


def _charts_text(report: dict) -> list[str]:
    c = report["charts"]
    sev, risks, time_ = c["severity"], c["risks"], c["time"]
    lines = [f"- Bugs by severity: {sev['high']} high, {sev['medium']} medium, {sev['low']} low",
             f"- Accepted risks tested: {risks['tested']} of {risks['tested'] + risks['untested']}"
             + (f" ({risks['untested_high']} high not tested)" if risks["untested_high"] else "")]
    if time_["split"]:
        lines.append(f"- Where the time went ({hours(time_['seconds'])}): "
                     + ", ".join(f"{PHASES[k].lower()} {round(v * 100)}%" for k, v in time_["split"].items()))
    return lines + [""]


def to_markdown(report: dict, view: str = "full") -> str:
    """view: "summary" (one screen: at a glance, charts, top bugs) or "full" (everything)."""
    title = f"# Test report: {report['project_name']}" + (" (summary)" if view == "summary" else "")
    out = [title, "", f"Period: {report['period']}. Generated {report['generated_at']:%Y-%m-%d %H:%M} UTC by Mirmera.", ""]
    if "glance" in report:
        out += [_totals(report), ""]
        if report.get("ai_summary"):
            out += ["## Summary", "", report["ai_summary"]["text"], "", f"_{_summary_label(report['ai_summary'])}_", ""]
        out += _glance_md(report)
    if view == "summary":
        out += _charts_text(report)
        tb = report["top_bugs"]
        if tb["rows"]:
            out += _blocks_md(report, [h("Top bugs"), table(tb["headers"], tb["rows"])])
            if tb["total"] > len(tb["rows"]):
                out += [f"{tb['total'] - len(tb['rows'])} more in the full report.", ""]
        return "\n".join(out)
    return "\n".join(out + _blocks_md(report, report["blocks"]))


def _cell_html(cell) -> str:
    if isinstance(cell, list):
        return ", ".join(_cell_html(c) for c in cell)
    if isinstance(cell, dict):
        return f'<a href="{html.escape(cell["href"])}">{html.escape(cell["text"])}</a>'
    return html.escape(str(cell))


STYLE = """
body { font: 14px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; color: #1b1d21; max-width: 960px; margin: 32px auto; padding: 0 24px; }
h1 { font-size: 24px; margin: 0 0 4px; } h2 { font-size: 18px; margin: 28px 0 8px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
h3 { font-size: 15px; margin: 18px 0 6px; } .meta { color: #666; font-size: 12.5px; margin-bottom: 6px; } .totals { font-size: 13px; margin-bottom: 14px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 6px 0 12px; }
th, td { text-align: left; padding: 5px 7px; border-bottom: 1px solid #e3e3e3; vertical-align: top; }
th { color: #555; font-weight: 600; } blockquote { margin: 8px 0; padding: 4px 12px; border-left: 3px solid #bbb; color: #333; }
blockquote footer { color: #777; font-size: 12px; } a { color: #1a5fb4; }
.glance { list-style: none; padding: 0; border: 1px solid #ddd; border-radius: 8px; }
.glance li { padding: 7px 12px; border-top: 1px solid #eee; } .glance li:first-child { border-top: none; }
.glance .d { color: #666; font-size: 12.5px; } .tone-danger b { color: #b42318; } .tone-warning b { color: #a15c07; } .tone-ok b { color: #067647; }
@media print { body { margin: 0; } a { color: inherit; text-decoration: none; } h2 { break-after: avoid; } tr { break-inside: avoid; } }
"""


def _blocks_html(report: dict, blocks: list[dict]) -> list[str]:
    out = []
    for b in blocks:
        t = b["type"]
        if t == "heading":
            out.append(f"<h{b['level']}>{html.escape(b['text'])}</h{b['level']}>")
        elif t == "paragraph":
            out.append(f"<p>{html.escape(b['text'])}</p>")
        elif t == "bullets":
            out.append("<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in b["items"]) + "</ul>")
        elif t == "actions":
            items = []
            for a in b["items"]:
                url = _app_link(report, a["to"])
                items.append(f"<li>{html.escape(a['text'])}" + (f' (<a href="{html.escape(url)}">{html.escape(a["label"])}</a>)' if url else "") + "</li>")
            out.append("<ul>" + "".join(items) + "</ul>")
        elif t == "quote":
            out.append(f"<blockquote>{html.escape(b['text'])}<footer>— {html.escape(b['by'])}</footer></blockquote>")
        elif t == "table":
            head = "".join(f"<th>{html.escape(x)}</th>" for x in b["headers"])
            rows = "".join("<tr>" + "".join(f"<td>{_cell_html(c)}</td>" for c in row) + "</tr>" for row in b["rows"])
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
    return out


def to_html(report: dict, view: str = "full") -> str:
    name = html.escape(report["project_name"])
    out = [f"<h1>Test report: {name}{' (summary)' if view == 'summary' else ''}</h1>",
           f'<div class="meta">Period: {html.escape(report["period"])}. Generated {report["generated_at"]:%Y-%m-%d %H:%M} UTC by Mirmera.</div>']
    if "summary" in report and "glance" in report:
        out.append(f'<div class="totals">{html.escape(_totals(report))}</div>')
        if report.get("ai_summary"):
            out.append(f'<h2>Summary</h2><p>{html.escape(report["ai_summary"]["text"])}</p>'
                       f'<div class="meta">{html.escape(_summary_label(report["ai_summary"]))}</div>')
        out.append('<h2>At a glance</h2><ul class="glance">')
        for g in report["glance"]:
            url = _app_link(report, g["to"])
            act = f' <a href="{html.escape(url)}">{html.escape(g["label"])}</a>' if url else ""
            out.append(f'<li class="tone-{g["tone"]}"><b>{html.escape(g["title"])}</b>{act}<div class="d">{html.escape(g["detail"])}</div></li>')
        out.append("</ul>")
    if view == "summary":
        out.append("<ul>" + "".join(f"<li>{html.escape(x[2:])}</li>" for x in _charts_text(report) if x) + "</ul>")
        tb = report["top_bugs"]
        if tb["rows"]:
            out += _blocks_html(report, [h("Top bugs"), table(tb["headers"], tb["rows"])])
            if tb["total"] > len(tb["rows"]):
                out.append(f"<p>{tb['total'] - len(tb['rows'])} more in the full report.</p>")
    else:
        out += _blocks_html(report, report["blocks"])
    title = html.escape(f"Test report: {report['project_name']}")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><style>{STYLE}</style></head>'
            f"<body>{''.join(out)}</body></html>")
