"""The coverage map: where in the app the testing actually went.

Two views, both from what was recorded:

- Pages: every page the recorded browser (or the extension) opened, with how
  many sessions went there, what was done on it (actions, distinct controls),
  what went wrong (failed requests, errors) and which bugs were logged there.
  Addresses are grouped by path, with ids collapsed (/orders/42 → /orders/:id),
  so staging and production, and order 41 and 42, count as one page.
- Product areas: the elements of the latest product model, each with the
  sessions whose charters target a risk naming it.

Then the gaps: pages seen but never tried, problems nobody logged a bug for,
pages not visited for a while, and product areas no session has touched.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy.orm import Session as DBSession

from app import models
from app.runner import describe_locator
from app.timeutil import utcnow

STALE_DAYS = 14
PROBLEM_KINDS = ("failed_request", "console_error", "page_error")
_ID = re.compile(r"^(\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{12,}|(?=.*\d)[A-Za-z0-9_-]{16,})$", re.I)


def page_key(url: str | None) -> str | None:
    """The page an address belongs to: its path, ids collapsed, no query or trailing slash."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    path = urlsplit(url).path or "/"
    parts = [":id" if _ID.match(p) else p for p in path.split("/") if p]
    return "/" + "/".join(parts)


def _host(url: str) -> str:
    return urlsplit(url).netloc


@dataclass
class Page:
    path: str
    hosts: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)
    testers: set[str] = field(default_factory=set)
    charters: set[str] = field(default_factory=set)
    visits: int = 0
    actions: int = 0
    controls: set[str] = field(default_factory=set)
    problems: dict[str, int] = field(default_factory=lambda: defaultdict(int))  # summary -> count
    bugs: list[dict] = field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def seen(self, ts: datetime) -> None:
        self.first_seen = min(self.first_seen or ts, ts)
        self.last_seen = max(self.last_seen or ts, ts)


def _control(action: dict | None) -> str | None:
    """What was used, without the value typed: 'fill label "Email"', 'click button "Verify"'."""
    if not action or not isinstance(action.get("locator"), dict):
        return None
    return f"{action.get('action', '?')} {describe_locator(action['locator'])}"


def _problem(e: models.Evidence) -> str:
    """Failed requests grouped by method, page key and status; errors by their first line."""
    if e.kind == "failed_request":
        m = re.match(r"(\w+) (\S+) → (.+)$", e.summary or "")
        if m:
            return f"{m.group(1)} {page_key(m.group(2)) or m.group(2)} → {m.group(3)}"
    return (e.summary or "").splitlines()[0][:160] if e.summary else e.kind


def build(db: DBSession, project: models.Project, since: datetime | None = None) -> dict:
    now = utcnow()
    charters = db.query(models.Charter).filter(models.Charter.project_id == project.id).all()
    sessions = [s for c in charters for s in c.sessions if since is None or s.started_at >= since]
    session_by_id = {s.id: s for s in sessions}
    pages: dict[str, Page] = {}

    def page(key: str) -> Page:
        return pages.setdefault(key, Page(key))

    evidence = (db.query(models.Evidence).filter(models.Evidence.session_id.in_(list(session_by_id)))
                .filter(models.Evidence.kind.in_(("navigation", "action") + PROBLEM_KINDS))
                .order_by(models.Evidence.session_id, models.Evidence.captured_at).all()) if sessions else []
    bugs = (db.query(models.Bug).filter(models.Bug.session_id.in_(list(session_by_id))).order_by(models.Bug.created_at).all()
            if sessions else [])
    bugs_by_session: dict[str, list[models.Bug]] = defaultdict(list)
    for b in bugs:
        bugs_by_session[b.session_id].append(b)

    by_session: dict[str, list[models.Evidence]] = defaultdict(list)
    for e in evidence:
        by_session[e.session_id].append(e)
    for sid, items in by_session.items():
        s = session_by_id[sid]
        current: str | None = None  # the page the tester was on
        timeline: list[tuple[datetime, str]] = []
        for e in items:
            key = page_key(e.url) if e.kind in ("navigation", "action") else None
            if key:
                current = key
                p = page(key)
                p.hosts.add(_host(e.url))
                p.sessions.add(sid)
                p.testers.add(s.tester_id)
                p.charters.add(s.charter.mission_statement)
                p.seen(e.last_seen_at or e.captured_at)
                timeline.append((e.captured_at, key))
                if e.kind == "navigation":
                    p.visits += e.count or 1
                else:
                    p.actions += e.count or 1
                    control = _control(e.data)
                    if control:
                        p.controls.add(control)
            elif e.kind in PROBLEM_KINDS and current:
                page(current).problems[_problem(e)] += e.count or 1
        for b in bugs_by_session.get(sid, []):  # the page the tester was on when logging it
            where = next((k for ts, k in reversed(timeline) if ts <= b.created_at), timeline[0][1] if timeline else None)
            if where:
                page(where).bugs.append({"id": b.id, "title": b.title, "severity": b.severity, "kind": b.kind})

    rows = []
    for p in sorted(pages.values(), key=lambda p: p.path):
        stale = p.last_seen is not None and now - p.last_seen > timedelta(days=STALE_DAYS)
        flags = []
        if p.actions == 0:
            flags.append("not_tried")
        if p.problems and not p.bugs:
            flags.append("problems_without_bug")
        if stale:
            flags.append("stale")
        if len(p.testers) == 1 and len(sessions) > 1:
            flags.append("one_tester")
        rows.append({
            "path": p.path, "hosts": sorted(p.hosts), "sessions": len(p.sessions), "testers": sorted(p.testers),
            "charters": sorted(p.charters), "visits": p.visits, "actions": p.actions, "controls": sorted(p.controls),
            "problems": [{"summary": k, "count": v} for k, v in sorted(p.problems.items(), key=lambda kv: -kv[1])],
            "bugs": p.bugs, "first_seen": p.first_seen, "last_seen": p.last_seen, "flags": flags,
        })

    return {"project_id": project.id, "since": since, "generated_at": now, "sessions": len(sessions),
            "recorded_sessions": len(by_session), "stale_days": STALE_DAYS,
            "pages": rows, "areas": (areas := _areas(db, project, sessions)), "gaps": _gaps(rows, areas)}


def _areas(db: DBSession, project: models.Project, sessions: list[models.Session]) -> list[dict]:
    """Each product-model element with the sessions whose charter targets a risk naming it."""
    model = (db.query(models.ProductModel).filter(models.ProductModel.project_id == project.id)
             .order_by(models.ProductModel.created_at.desc()).first())
    if not model:
        return []
    risks_by_element: dict[str, list[models.Risk]] = defaultdict(list)
    for r in project.risks:
        if r.status != "retired":
            for name in r.elements or []:
                risks_by_element[name.strip().lower()].append(r)
    out = []
    for el in model.elements or []:
        name = el.get("name", "")
        risks = risks_by_element.get(name.strip().lower(), [])
        risk_ids = {r.id for r in risks}
        ran = [s for s in sessions if any(r.id in risk_ids for r in s.charter.risks)]
        out.append({"category": el.get("category", "other"), "name": name, "description": el.get("description", ""),
                    "risks": [f"{r.ref} {r.title}" for r in risks], "sessions": len(ran),
                    "last_tested": max((s.started_at for s in ran), default=None)})
    return out


def _gaps(rows: list[dict], areas: list[dict]) -> list[str]:
    gaps = []
    for a in areas:
        if not a["sessions"]:
            why = "no risk names it" if not a["risks"] else f"no session on {', '.join(a['risks'])}"
            gaps.append(f"Product area not explored: {a['name']} ({a['category']}): {why}")
    for r in rows:
        if "problems_without_bug" in r["flags"]:
            top = r["problems"][0]
            gaps.append(f"{r['path']}: {top['summary']} (x{top['count']}) was recorded but no bug was logged there")
    for r in rows:
        if "not_tried" in r["flags"]:
            gaps.append(f"{r['path']}: opened but nothing was tried on it")
    for r in rows:
        if "stale" in r["flags"]:
            gaps.append(f"{r['path']}: last visited {r['last_seen']:%Y-%m-%d}")
    return gaps
