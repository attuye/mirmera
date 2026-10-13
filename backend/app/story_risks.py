"""Suggest risks for one story: the project's risks the change touches, and the new ones it brings in.

The AI reads the story with the project's product model and risks, so it can say "this change
touches R3" rather than proposing R3 again under another name. Everything is a proposal:
- a new risk is made "proposed" and linked to the story; the tester accepts or drops it;
- a touched risk is only suggested; the tester links it or drops the suggestion.
A new risk whose title is nearly an existing one's becomes a link suggestion instead.
The Jira app's planning uses the same code (jira_agent.plan).
"""
import re

from sqlalchemy.orm import Session as DBSession

from app import models
from app.ai import tasks as ai_tasks
from app.ai.routing import provider_for

MIN_DESCRIPTION = 20  # characters: less than this and the AI could only invent


class StoryTooThin(Exception):
    """The story has nothing to analyse. The message says what to add."""


def _words(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", title.lower()))


def same_risk(a: str, b: str) -> bool:
    """Two risk titles that say the same thing: equal, or nearly the same words."""
    wa, wb = _words(a), _words(b)
    if a.strip().casefold() == b.strip().casefold():
        return True
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.8


def _model_of(db: DBSession, project_id: str) -> dict | None:
    snap = (db.query(models.ProductModel).filter(models.ProductModel.project_id == project_id)
            .order_by(models.ProductModel.created_at.desc()).first())
    if not snap:
        return None
    return {"summary": snap.summary, "elements": [{"category": e.get("category"), "name": e.get("name")}
                                                  for e in (snap.elements or [])][:60]}


async def suggest(db: DBSession, project: models.Project, story: models.Story, *, max_risks: int = 5,
                  provider_override: str | None = None, why: str | None = None) -> dict:
    """Asks the AI, saves the new risks as proposals on the story, and returns what it suggested:
    {"story_id", "thin", "new": [{risk_id, ref, title}], "links": [{risk_id, ref, title, score, reason, status}]}."""
    if len((story.description or "").strip()) < MIN_DESCRIPTION:
        raise StoryTooThin("The story has no description to find risks in. Add what it should do and its "
                           "acceptance criteria (or refresh it from the tracker), then suggest risks.")
    existing = (db.query(models.Risk).filter(models.Risk.project_id == project.id, models.Risk.status != "retired")
                .order_by(models.Risk.created_at).all())
    linked = {r.id for r in story.risks}
    by_ref = {r.ref: r for r in existing}
    provider = provider_for(project, "risk_analysis", provider_override)
    story_text = {"key": story.key, "title": story.title, "description": story.description}
    model = _model_of(db, project.id)
    project_risks = [{"ref": r.ref, "title": r.title, "failure_mode": r.failure_mode, "quality_criterion": r.quality_criterion,
                      "score": r.score, "linked": r.id in linked} for r in existing]
    db.commit()  # no transaction stays open while the AI thinks: other writes would have to wait
    answer = await ai_tasks.story_risks(provider, project_name=project.name, testing_mission=project.testing_mission,
                                        story=story_text, product_model=model, project_risks=project_risks,
                                        max_risks=max_risks)

    links: list[dict] = []

    def suggest_link(risk: models.Risk, reason: str) -> None:
        if risk.id not in linked and risk.id not in {x["risk_id"] for x in links}:
            links.append({"risk_id": risk.id, "ref": risk.ref, "title": risk.title, "score": risk.score,
                          "reason": reason.strip()[:400], "status": "suggested"})

    for t in answer.touches:
        risk = by_ref.get(t.risk_ref.strip().upper())
        if risk:  # the model may only name risks it was given
            suggest_link(risk, t.reason)

    from app.routers.risks import _next_ref_number
    number = _next_ref_number(db, project.id)
    made: list[models.Risk] = []
    for r in sorted(answer.new_risks, key=lambda r: r.impact * r.likelihood, reverse=True):
        twin = next((x for x in existing if same_risk(x.title, r.title)), None)
        if twin:
            suggest_link(twin, f'Proposed as a new risk ("{r.title}"), but {twin.ref} already covers it.')
            continue
        if any(same_risk(x.title, r.title) for x in made):
            continue
        risk = models.Risk(project_id=project.id, ref=f"R{number + len(made)}", **r.model_dump(), status="proposed",
                           source="ai", ai_provider=provider.name, ai_model=provider.model,
                           prompt_version=ai_tasks.PROMPT_VERSIONS["story_risks"],
                           tester_notes=why or f"Suggested from story {story.key or story.title}.")
        db.add(risk)
        story.risks.append(risk)
        made.append(risk)
    db.commit()
    return {"story_id": story.id, "thin": (answer.thin or "").strip() or None,
            "new": [{"risk_id": r.id, "ref": r.ref, "title": r.title} for r in made], "links": links}
