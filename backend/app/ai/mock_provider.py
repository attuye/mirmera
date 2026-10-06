"""
A free, zero-latency provider for development and tests, no API key needed.
It returns realistic, deterministic answers shaped by whatever was sent, not
random lorem ipsum, so the whole app (charter review, session flow, debrief
editing) can be built and tested end to end before spending anything.

Switch to a real model by setting DEFAULT_AI_PROVIDER in backend/.env
(claude, openai or local). Nothing else changes.
"""
import json
import re

from app.ai.base import LLMProvider


def _between(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>\n?(.*?)\n?</{tag}>", text, re.S)
    return match.group(1).strip() if match else ""


def _first_line(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("+++", "---", "@@", "[")):
            return line[:80]
    return fallback


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock"

    async def complete(self, system, user, *, max_tokens=2000, schema=None, task=None) -> str:
        if task == "charter_suggestions":
            return json.dumps(self._charters(user))
        if task == "debrief":
            return json.dumps(self._debrief(user))
        if task == "artifact_summary":
            return self._summary(user)
        if task == "product_model":
            return json.dumps(self._product_model(user))
        if task == "risk_analysis":
            return json.dumps(self._risks(user))
        if task == "risk_charters":
            return json.dumps(self._risk_charters(user))
        if task == "bug_report":
            return json.dumps(self._bug_report(user))
        if task == "risk_update":
            return json.dumps(self._risk_update(user))
        if task == "duplicate_check":
            return json.dumps(self._duplicates(user))
        if task == "regression_test":
            return json.dumps(self._regression(user))
        if task == "regression_checks":
            plan = self._regression(user)
            return json.dumps({k: plan[k] for k in ("test_title", "assertions")} | {
                "assumptions": ["(mock) The steps were fixed before the AI was asked; check the assertion matches the oracle."]})
        if task == "reproduce_step":
            return json.dumps(self._reproduce_step(user))
        if task == "explore_step":
            return json.dumps(self._explore_step(user))
        if task == "report_summary":
            return json.dumps(self._report_summary(user))
        if task == "test_strategy":
            return json.dumps(self._strategy(user))
        return json.dumps({"note": f"mock provider has no canned answer for task '{task}'"})

    @staticmethod
    def _strategy(user: str) -> dict:
        """One sentence per section from its own fact lines, so its numbers are always grounded."""
        sections, key = {}, None
        for line in _between(user, "facts").splitlines():
            if line.startswith("[") and line.endswith("]"):
                key = line[1:-1]
                sections[key] = []
            elif key and line.startswith("- "):
                sections[key].append(line[2:])
        out = {}
        for k, lines in sections.items():
            if k == "out_of_scope":
                out[k] = "(mock) " + (" ".join(f"Do we leave out {l.split(': ', 1)[-1]}?" for l in lines[:3])
                                      or "What has the team agreed not to test?")
            else:
                out[k] = "(mock) " + ("; ".join(lines[:3]) + "." if lines else "Nothing recorded yet.")
        return {"sections": out}

    @staticmethod
    def _report_summary(user: str) -> dict:
        """Built only from the fact lines, so its numbers are always grounded."""
        facts = [re.sub(r"^\d+\. ", "", l) for l in _between(user, "facts").splitlines() if l.strip()]
        glance = [f for f in facts if f.startswith("At a glance: ")]
        totals = next((f for f in facts if f.startswith("Totals: ")), "")
        lead = glance[0].removeprefix("At a glance: ") if glance else "Nothing was flagged"
        text = f"(mock) {lead}. {totals.removeprefix('Totals: ')}." if totals else f"(mock) {lead}."
        return {"summary": text, "basis": ([glance[0]] if glance else []) + ([totals] if totals else [])}

    @staticmethod
    def _charters(user: str) -> dict:
        artifacts = _between(user, "artifacts")
        project = _between(user, "project") or "this product"
        hook = _first_line(artifacts, project)
        # Anything that looks like a changed function/file name keeps the mock
        # grounded in what was actually pasted, not generic filler.
        touched = re.findall(r"(?:def |class |\+\+\+ b/|@@.*)([a-zA-Z_][\w./]*)", artifacts)
        focus = touched[0] if touched else "the changed area"
        return {"suggestions": [
            {
                "mission_statement": f"Explore {focus} for behavior changes implied by: {hook}",
                "rationale": (
                    "Mock suggestion: stands in for the AI call so you can build and test "
                    "the charter review flow without API billing. Real suggestions read the "
                    "full artifact content and reference specific risk patterns."
                ),
                "risk_areas": ["needs review"],
                "priority": "high",
            },
            {
                "mission_statement": f"Regression-check areas adjacent to {focus}",
                "rationale": "Mock suggestion: switch to a real provider for context-grounded rationale.",
                "risk_areas": ["regression"],
                "priority": "medium",
            },
            {
                "mission_statement": f"Probe edge cases and boundary conditions around {focus}",
                "rationale": "Mock suggestion: placeholder priority and rationale until a real provider is used.",
                "risk_areas": ["boundary value"],
                "priority": "medium",
            },
        ]}

    @staticmethod
    def _debrief(user: str) -> dict:
        charter = _between(user, "charter") or "the session charter"
        lines = [line for line in _between(user, "notes").splitlines() if line.strip()]
        lines = [] if lines == ["(no notes logged)"] else lines
        tagged = [(re.search(r"\[(\w+)\]", l).group(1) if re.search(r"\[(\w+)\]", l) else "test",
                   l.split("] ", 1)[-1]) for l in lines]
        bugs = [b.lstrip("- ") for b in _between(user, "bugs").splitlines() if b.strip() and b.strip() != "(none)"]
        bug_text = "; ".join(bugs) if bugs else "(none)"
        browser = _between(user, "browser")
        recorded = next((l for l in browser.splitlines() if l.startswith("Recorded:")), None)
        unreachable = [l.split("] ", 1)[-1] for l in browser.splitlines() if "] Could not open " in l]
        return {
            "charter_status": "partially_fulfilled" if lines else "not_fulfilled",
            "charter_status_rationale": f"Mock: {len(lines)} note(s) logged against the charter.",
            "past": (
                f'Mock debrief for charter: "{charter}". {len(lines)} note(s) logged this session. '
                f"Bugs found: {bug_text}. Switch DEFAULT_AI_PROVIDER to a real provider for a synthesized narrative."
            ),
            "results": ([f"Logged: {b}" for b in bugs] or ["(mock) No bugs logged"])
                       + ([f"(mock) Browser {recorded[0].lower()}{recorded[1:]}"] if recorded else []),
            "obstacles": [text for tag, text in tagged if tag in ("issue", "setup")] + unreachable,
            "outlook": [{
                "charter": f"Explore the edges of {charter[:60]} with unusual inputs to discover what the session missed",
                "reason": "Mock follow-up: the session only covered part of the charter.",
            }],
            "risks_observed": [f"(mock) Problems like '{bugs[0][:60]}' may recur elsewhere"] if bugs else [],
            "open_questions": [text for tag, text in tagged if tag == "question"],
        }

    @staticmethod
    def _summary(user: str) -> str:
        match = re.search(r'<artifact type="([^"]+)">\n?(.*?)\n?</artifact>', user, re.S)
        kind, content = (match.group(1), match.group(2)) if match else ("artifact", user)
        first = _first_line(content, "(empty artifact)")
        return f"Mock summary of this {kind}: starts with \u201c{first}\u201d ({len(content)} chars total)."
    # ---- risk model ----

    @staticmethod
    def _product_model(user: str) -> dict:
        artifacts = re.findall(r'<artifact type="([^"]+)" label="([^"]+)">\n?(.*?)\n?</artifact>', user, re.S)
        labels = [label for _, label, _ in artifacts] or ["(no artifacts)"]
        text = "\n".join(content for _, _, content in artifacts)
        names = list(dict.fromkeys(re.findall(r"(?:def |class |\+\+\+ b/)([a-zA-Z_][\w./]*)", text)))[:3]
        names = names or ["the changed area"]
        elements = [
            {"category": "function", "name": name, "description": f"Mock: behaviour of {name}", "sources": labels}
            for name in names
        ]
        elements.append({
            "category": "data", "name": "inputs and stored data",
            "description": "Mock: values flowing through the changed code", "sources": labels,
        })
        return {
            "summary": (
                f"Mock product model built from {len(artifacts)} artifact(s). "
                "Switch to a real provider for an SFDIPOT model grounded in the content."
            ),
            "elements": elements,
            "unknowns": [f"(mock) What should happen when {names[0]} receives unexpected input?"],
        }

    @staticmethod
    def _risks(user: str) -> dict:
        try:
            model = json.loads(_between(user, "product_model"))
            names = [e["name"] for e in model.get("elements", [])] or ["the product"]
        except (ValueError, TypeError):
            names = ["the product"]
        first, last = names[0], names[-1]
        return {"risks": [
            {
                "title": f"Wrong results from {first}",
                "failure_mode": f"Users get incorrect results when {first} handles edge-case input",
                "quality_criterion": "capability", "elements": [first],
                "impact": 4, "likelihood": 3,
                "rationale": "Mock risk: recently changed logic with unclear edge cases.",
                "evidence": "inferred",
            },
            {
                "title": f"Data exposure through {last}",
                "failure_mode": f"Sensitive data in {last} is visible to users who should not see it",
                "quality_criterion": "security", "elements": [last],
                "impact": 5, "likelihood": 2,
                "rationale": "Mock risk: high harm if it happens, no evidence it does.",
                "evidence": "speculative",
            },
            {
                "title": f"Slow responses from {first} under load",
                "failure_mode": f"{first} becomes slow when many users act at once",
                "quality_criterion": "performance", "elements": [first],
                "impact": 2, "likelihood": 2,
                "rationale": "Mock risk: low priority placeholder.",
                "evidence": "speculative",
            },
        ]}

    @staticmethod
    def _risk_charters(user: str) -> dict:
        try:
            risks = json.loads(_between(user, "risks"))
        except (ValueError, TypeError):
            risks = []
        refs = [r["ref"] for r in risks] or ["R1"]
        groups = [refs[:2], refs[2:]] if len(refs) > 2 else [refs]
        charters = []
        for group in groups:
            titles = [r["title"] for r in risks if r["ref"] in group] or ["the riskiest area"]
            charters.append({
                "target": titles[0].lower(),
                "resources": "boundary values and realistic test data",
                "information": f"whether {', '.join(group)} can actually happen",
                "rationale": f"Mock charter covering {', '.join(group)}.",
                "risk_refs": group,
                "test_ideas": ["Try the smallest and largest valid values", "Repeat the flow after a page reload"],
                "oracles": ["claims", "user_expectations"],
                "priority": "high" if group == groups[0] else "medium",
            })
        return {"charters": charters}

    # ---- bug reports ----

    @staticmethod
    def _explore_step(user: str) -> dict:
        """A small, predictable explorer: notes what it sees, reports a failed request it is
        shown (once), fills the first text field, clicks the first button, then finishes."""
        page = _between(user, "page")
        history = [l for l in _between(user, "history").splitlines() if re.match(r"\d+\. ", l)]
        done_before = " ".join(history)
        if "Steps left: 1." in user:
            return {"thought": "(mock) Out of steps.", "action": {"kind": "done", "summary": "(mock) Ran out of steps."}}
        failed = re.search(r"\[failed request\] (\w+) (\S+) -[>›] (\S+)", page)  # the page text is defanged
        if failed and "problem:" not in done_before:
            path = re.sub(r"^https?://[^/]+", "", failed.group(2)).split("?")[0] or "/"
            return {"thought": "(mock) A request failed while I explored.", "action": {"kind": "problem", "problem": {
                "title": f"{failed.group(1)} {path} fails with {failed.group(3)}",
                "expected": "The app's requests succeed while a customer uses this page.",
                "actual": f"{failed.group(1)} {path} answered {failed.group(3)}.",
                "severity": "high" if failed.group(3).startswith("5") else "medium",
                "symptom": {"kind": "failed_request", "value": path,
                            "explanation": "The failing request is listed under what the page reported."}}}}
        if not history:
            title = next((l[7:] for l in page.splitlines() if l.startswith("Title: ")), "the page")
            return {"thought": "(mock) Starting the charter.", "action": {"kind": "note", "note": f"(mock) Opened {title}."}}
        field = re.search(r'- textbox "([^"]+)"', page)
        if field and "fill " not in done_before:
            return {"thought": "(mock) Try a long, non-Latin name.", "action": {
                "kind": "fill", "locator": {"method": "role", "role": "textbox", "name": field.group(1)}, "value": "አበበ ከበደ"}}
        button = re.search(r'- button "([^"]+)"', page)
        if button and "click " not in done_before:
            return {"thought": "(mock) Submit and watch what comes back.", "action": {
                "kind": "click", "locator": {"method": "role", "role": "button", "name": button.group(1)}}}
        return {"thought": "(mock) Nothing more to try.", "action": {
            "kind": "done", "summary": "(mock) Filled the first field with an Amharic name and submitted it. "
                                       "Not covered: other fields, boundaries, repeats."}}

    @staticmethod
    def _reproduce_step(user: str) -> dict:
        """Follows the recorded actions one per step, then claims the symptom the
        recording points to (a failed request, an error, else the actual text)."""
        bug = json.loads(_between(user, "bug") or "{}")
        recorded = _between(user, "recorded")
        hints_line = next((l for l in recorded.splitlines() if l.startswith("[")), "[]")
        try:
            hints = json.loads(hints_line.replace("‹", "<").replace("›", ">"))
        except ValueError:
            hints = []
        usable = [h for h in hints if h.get("action") in ("click", "fill", "select", "check", "uncheck", "press")
                  and (h.get("locator") or {}).get("method") not in (None, "css") and not h.get("in_frame")]
        history = [l for l in _between(user, "history").splitlines() if re.match(r"\d+\. ", l)]
        if any("-> not verified" in l for l in history):
            return {"thought": "(mock) My claim could not be checked.",
                    "action": {"kind": "give_up", "reason": "(mock) the symptom I expected did not show"}}
        if len(history) < len(usable):
            h = usable[len(history)]
            act = {"click": {"kind": "click"}, "check": {"kind": "check"}, "uncheck": {"kind": "uncheck"},
                   "select": {"kind": "select", "value": h.get("value")},
                   "press": {"kind": "press", "key": h.get("key") or "Enter"},
                   "fill": ({"kind": "fill_secret", "secret": h.get("secret")} if h.get("masked")
                            else {"kind": "fill", "value": h.get("value", "")})}[h["action"]]
            return {"thought": f"(mock) Repeat recorded action {len(history) + 1} of {len(usable)}.",
                    "action": {**act, "locator": h["locator"]}}
        page = _between(user, "page")
        text = page + "\n" + recorded
        failed = re.search(r"\[failed request[^\]]*\] \w+ (https?://\S+)", text)
        console = re.search(r"\[console error[^\]]*\] (.{1,80})", text)
        page_err = re.search(r"\[page error[^\]]*\] (.{1,80})", text)
        if failed:
            path = re.sub(r"^https?://[^/]+", "", failed.group(1)) or failed.group(1)
            symptom = {"kind": "failed_request", "value": path}
        elif page_err:
            symptom = {"kind": "page_error", "value": page_err.group(1).strip()[:60]}
        elif console:
            symptom = {"kind": "console_error", "value": console.group(1).strip()[:60]}
        elif bug.get("actual"):
            symptom = {"kind": "text_visible", "value": bug["actual"][:60]}
        else:
            return {"thought": "(mock) Nothing recorded to go on.",
                    "action": {"kind": "give_up", "reason": "(mock) no recorded actions or signals to follow"}}
        return {"thought": "(mock) The recorded actions are done; check the symptom.",
                "action": {"kind": "reproduced",
                           "symptom": {**symptom, "explanation": "(mock) This is what the recording showed."}}}

    @staticmethod
    def _regression(user: str) -> dict:
        """Replays every recorded action; asserts no failed request (or no
        console error) when the page reported one, else the expected text."""
        report = json.loads(_between(user, "report") or "{}")
        actions = [l for l in _between(user, "actions").splitlines() if l.startswith("[")]
        pages = [l for l in _between(user, "pages").splitlines() if l.startswith("http")]
        browser = _between(user, "browser")
        failed = re.search(r"\[failed request[^\]]*\] \w+ (\S+) →", browser)
        steps = ([{"kind": "goto", "url": pages[0], "why": "(mock) Open the first page visited"}] if pages else [])
        steps += [{"kind": "recorded", "action_index": i, "why": ""} for i in range(len(actions))]
        if not steps:
            steps = [{"kind": "goto", "url": "http://localhost:3000/", "why": "(mock) No pages were recorded"}]
        if failed:
            path = re.sub(r"^https?://[^/]+", "", failed.group(1)) or failed.group(1)
            assertion = {"kind": "no_failed_requests", "url_contains": path,
                         "why": f"(mock) The page reported {path} failing; it should succeed"}
        elif "[console error" in browser or "[page error" in browser:
            assertion = {"kind": "no_console_errors", "why": "(mock) The page logged errors; it should not"}
        else:
            assertion = {"kind": "text", "locator": {"method": "role", "role": "status"},
                         "expected": report.get("expected") or "OK", "why": "(mock) The expected result from the report"}
        return {"test_title": report.get("title") or "regression", "steps": steps, "assertions": [assertion],
                "assumptions": ["(mock) Every recorded action was kept; remove the ones that do not lead to the bug."]}

    @staticmethod
    def _duplicates(user: str) -> dict:
        """Word overlap between titles: half or more is a likely duplicate."""
        from app.trackers.base import keywords
        report = json.loads(_between(user, "report") or "{}")
        mine = set(keywords(report.get("title", ""), limit=20))
        verdicts = []
        for c in json.loads(_between(user, "candidates") or "[]"):
            theirs = set(keywords(c["title"], limit=20))
            share = len(mine & theirs) / max(1, min(len(mine), len(theirs)))
            verdict = "likely_duplicate" if share >= 0.5 else "related" if share > 0 else "different"
            reason = f"(mock) {len(mine & theirs)} title word(s) in common: {', '.join(sorted(mine & theirs)) or 'none'}"
            if verdict == "likely_duplicate" and c.get("status") == "closed":
                reason += "; closed: possible regression"
            verdicts.append({"key": c["key"], "verdict": verdict, "reason": reason})
        return {"verdicts": verdicts}

    @staticmethod
    def _risk_update(user: str) -> dict:
        """Raises likelihood by 1 per risk when the session logged a bug
        (questions don't count); everything else stays unchanged."""
        risks = json.loads(_between(user, "risks") or "[]")
        bugs = [b for b in _between(user, "bugs").splitlines() if b.startswith("- [") and not b.startswith("- [question")]
        updates, unchanged = [], []
        for r in risks:
            if bugs and r["likelihood"] < 5:
                updates.append({
                    "risk_ref": r["ref"], "impact": r["impact"], "likelihood": r["likelihood"] + 1,
                    "reason": f"(mock) The session logged {len(bugs)} bug(s) while exercising this charter.",
                    "evidence": bugs[:2],
                })
            else:
                unchanged.append({"risk_ref": r["ref"], "reason": "(mock) No bug logged in this session."
                                  if not bugs else "(mock) Likelihood is already at the maximum."})
        return {"updates": updates, "unchanged": unchanged}

    @staticmethod
    def _bug_report(user: str) -> dict:
        note = _between(user, "note") or "(empty note)"
        title = note.splitlines()[0][:120]
        trail = [line.split("] ", 1)[-1].split(" ", 1)[-1] for line in _between(user, "trail").splitlines()
                 if line.startswith("[")]
        steps = trail[-4:] or ["(mock) Steps could not be recovered from the session notes"]
        browser = _between(user, "browser")
        problems = [l for l in browser.splitlines() if re.match(r"\+\d+ min \[(console error|page error|failed request)", l)]
        pages = [l for l in browser.splitlines() if re.match(r"\+\d+ min \[page\]", l)]
        if pages:
            steps = [f"Open {pages[-1].split('] ', 1)[-1]}"] + [s for s in steps if not s.startswith("(mock)")]
        if title.rstrip().endswith("?"):  # a note phrased as a question stays a question
            return {
                "kind": "question", "title": title, "summary": f"Mock: the tester asks: {title}",
                "steps_to_reproduce": steps, "expected": "(unclear: no oracle identified)",
                "actual": "(mock) as described in the note", "oracle": None,
                "severity": "low", "severity_rationale": "Mock: unknown until the question is answered.",
                "reproducibility": "unknown", "open_questions": [title],
            }
        return {
            "kind": "bug", "title": title, "summary": f"Mock report drafted from the note: {title}",
            "steps_to_reproduce": steps, "expected": "(mock) The behaviour described in the spec",
            "actual": f"(mock) {title}" + (f". The browser recorded: {problems[0].split('] ', 1)[-1]}" if problems else ""),
            "oracle": {"heuristic": "claims", "explanation": "Mock: the spec or product claims say otherwise."},
            "severity": "medium", "severity_rationale": "Mock: switch to a real provider for a risk-based rationale.",
            "reproducibility": "unknown",
            "open_questions": ["(mock) Does it happen every time?"],
            "evidence_used": problems[:1],
        }
