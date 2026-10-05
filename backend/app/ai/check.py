"""Check a model: run each AI task once with small, realistic, made-up input (the
practice app's KYC story) through the real task code, and report whether the
reply was usable first time, needed the retry, or failed, how long it took, and
a glimpse of what came back. Nothing is saved."""
import json
import time

from app import schemas
from app.ai import tasks
from app.ai.base import LLMProvider, ProviderError, get_provider
from app.ai.llm_json import complete_json

PROJECT = "Practice KYC"
MISSION = "Find release blockers in ID verification"
SPEC = ("Customers verify their identity with a National ID, a Passport or a Driver's licence. "
        "Names may be written in Ge'ez or Latin script. Expired documents must be rejected. "
        "After verifying, My documents lists every verified document.")
REPORT = {"title": "Passport verification fails", "steps": ["Open /verify", "Fill the form", "Choose Passport", "Click Verify"],
          "expected": "Verified. Welcome to AddisVerify!", "actual": "Something went wrong. Please try again later.",
          "oracle": "claims", "why_its_a_problem": "The home page says Passports are accepted", "severity": "high"}
RISKS = [{"ref": "R1", "title": "Passports can't be verified", "failure_mode": "A customer with a Passport can't open an account",
          "quality_criterion": "capability", "score": 15, "evidence": "stated", "tester_notes": ""}]
BROWSER = "[failed request] POST http://localhost:4000/api/verify → 500 (×1)"
NOTES = ["+2 min [test] Verified with a National ID: works",
         "+5 min [bug] Passport verification fails: Something went wrong",
         "+8 min [question] Is the phone number optional?"]


class Counting(LLMProvider):
    """Wraps a provider to count calls: two calls means the first reply was unusable."""

    def __init__(self, inner: LLMProvider):
        self.inner, self.calls = inner, 0
        self.name, self.model = inner.name, inner.model

    async def complete(self, system, user, **kwargs):
        self.calls += 1
        return await self.inner.complete(system, user, **kwargs)


async def _run(task: str, p: LLMProvider) -> str:
    """Runs one task; returns a short sample of the reply."""
    if task == "artifact_summary":
        return (await tasks.summarize_artifact(p, raw_content=SPEC, artifact_type="spec"))[:160]
    if task == "product_model":
        m, _ = await tasks.build_product_model(p, project_name=PROJECT, testing_mission=MISSION, artifacts=[("spec", "spec.md", SPEC)])
        return f"{len(m.elements)} elements, e.g. {m.elements[0].name}" if m.elements else m.summary[:160]
    if task == "risk_analysis":
        model = {"summary": SPEC, "elements": [{"category": "function", "name": "Document verification", "description": SPEC}], "unknowns": []}
        risks = await tasks.analyze_risks(p, project_name=PROJECT, testing_mission=MISSION, product_model=model, max_risks=3, existing_titles=[])
        return "; ".join(r.title for r in risks)[:160] or "(no risks)"
    if task == "risk_charters":
        charters = await tasks.charters_from_risks(p, project_name=PROJECT, testing_mission=MISSION, product_summary=SPEC, risks=RISKS, max_charters=2)
        return (charters[0].target if charters else "(no charters)")[:160]
    if task == "charter_suggestions":
        s = await tasks.suggest_charters(p, project_name=PROJECT, artifact_context=SPEC, recent_charters="(none)")
        return (s[0].mission_statement if s else "(no suggestions)")[:160]
    if task == "debrief":
        d = await tasks.draft_debrief(p, charter_mission="Explore ID verification with every document type", risks=RISKS,
                                      duration_minutes=20, notes=NOTES, bugs=["Passport verification fails"],
                                      time_breakdown={"on_charter": 0.8, "bug_investigation": 0.2}, browser=BROWSER)
        return f"{d.charter_status}: {d.past}"[:160]
    if task == "bug_report":
        d = await tasks.draft_bug_report(p, charter_mission="Explore ID verification", risks=RISKS,
                                         note="Passport verification fails", trail=[{"tag": "test", "content": "National ID works"}],
                                         environment={"browser": "Chromium"}, browser=BROWSER)
        return f"{d.kind}: {d.title}" + (f" (oracle: {d.oracle.heuristic})" if d.oracle else " (no oracle)")
    if task == "risk_update":
        u = await tasks.suggest_risk_updates(p, charter_mission="Explore ID verification", risks=RISKS, notes=NOTES,
                                             bugs=["Passport verification fails"], browser=BROWSER)
        return (f"{u.updates[0].risk_ref} → impact {u.updates[0].impact}, likelihood {u.updates[0].likelihood}"
                if u.updates else f"{len(u.unchanged)} unchanged")
    if task == "duplicate_check":
        r = await tasks.check_duplicates(p, report=REPORT, candidates=[
            {"key": "#12", "title": "Passport upload returns 500", "status": "open", "snippet": "Verify fails for passports"},
            {"key": "#7", "title": "Dark mode colours", "status": "closed", "snippet": ""}])
        return ", ".join(f"{v.key} {v.verdict}" for v in r.verdicts) or "(no verdicts)"
    if task == "regression":
        c = await tasks.write_regression_checks(p, report=REPORT, browser=BROWSER, steps=[
            "1. open http://localhost:4000/verify", '2. fill label "Full name" = "Abebe Kebede"',
            '3. select "Passport" in label "Document type"', '4. check label "I accept the terms"', '5. click button "Verify"'])
        return f"{c.test_title}: " + ", ".join(a.kind for a in c.assertions)
    if task == "agent":
        from app.agent import reproduce as agent
        ctx = agent.BugContext(report=REPORT, hints=[], pages=["http://localhost:4000/verify"], signals=BROWSER)
        page = ('URL: http://localhost:4000/verify\nTitle: Verify identity\nNew since the last step:\n(nothing)\n'
                'Page structure (accessibility tree):\n- textbox "Full name"\n- combobox "Document type"\n'
                '- checkbox "I accept the terms"\n- button "Verify"')
        step = await complete_json(p, agent.PROMPT, agent._user_message(ctx, [], page, 20), schemas.LLMAgentStep,
                                   task="reproduce_step", max_tokens=800)
        return f"{step.action.kind}: {step.thought}"[:160]
    if task == "report_summary":
        r = await tasks.summarize_report(p, project_name=PROJECT, facts=[
            "Period: 2026-09-05 to 2026-10-04", "Totals: 9 sessions (11 h 20 min, 3 testers) · 6 bugs (2 high, 3 medium, 1 low)",
            "At a glance: 2 high bugs still open: Passport verification fails · Ge'ez names rejected at signup",
            "At a glance: 1 high risk not tested: R3 Selfie liveness accepts printed photos (score 20)",
            "At a glance: 1 fix verified after the issue closed: KYC-12 Expired national ID accepted",
            "Accepted risks tested: 4 of 6"])
        return r.summary[:160]
    if task == "test_strategy":
        r = await tasks.write_strategy(p, project_name=PROJECT, focus="Sprint 14", facts={
            "mission": ["Testing mission: find what stops a customer verifying with a passport"],
            "risks": ["R3 Selfie liveness accepts printed photos (score 20, security): not explored in this sprint",
                      "R1 Expired IDs pass (score 15, capability): explored"],
            "out_of_scope": ["Candidate (retired risk): R7 Fax upload"]})
        return next(iter(r.sections.values()), "")[:160]
    raise ValueError(f"Unknown task {task}")


async def check(provider_name: str, task: str, keys=None) -> dict:
    started = time.monotonic()
    base = {"provider": provider_name, "task": task}
    try:
        p = Counting(get_provider(provider_name, keys))
    except ProviderError as err:
        return {**base, "result": "failed", "seconds": 0.0, "detail": str(err)}
    base["model"] = p.model
    try:
        sample = await _run(task, p)
        result = "ok" if p.calls <= 1 else "retried"
        return {**base, "result": result, "seconds": round(time.monotonic() - started, 1), "sample": sample,
                "detail": "The first reply was unusable; the second worked." if result == "retried" else None}
    except Exception as err:  # a provider error, or a reply that stayed unusable
        text = str(err).split("Details: ", 1)[-1]
        return {**base, "result": "failed", "seconds": round(time.monotonic() - started, 1), "detail": text[:400]}
