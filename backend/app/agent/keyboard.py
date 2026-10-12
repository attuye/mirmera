"""The keyboard-only tour: the form again, with the Tab key and Enter only, as a user who can't
use a mouse does it (and many fast typists).

What Mirmera checks, without AI:
- every field, the button that sends the form, and anything else made clickable (a role="button",
  a custom checkbox) can be reached with Tab;
- the focused element shows it has focus (an outline, a ring, a colour change);
- Tab never gets stuck on one element (a keyboard trap);
- the form can be sent from the keyboard, with the same answer the mouse got.

Each problem is a draft whose symptom Mirmera can check again in a fresh browser: the tab walk
is done again and the claim ("unreachable:Send") is checked against it.
"""
import re

MAX_TABS = 60

# Marks every element a user might act on, with how it looks unfocused (to see focus appear).
MARK_JS = """() => {
  const visible = (el) => {
    if (el.closest('[hidden], [aria-hidden="true"]')) return false
    const s = getComputedStyle(el), r = el.getBoundingClientRect()
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0
  }
  const text = (t) => (t || '').replace(/\\s+/g, ' ').trim()
  const labelOf = (el) => {
    const byFor = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
    return text((byFor && byFor.innerText) || (el.closest('label') && el.closest('label').innerText)
      || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.innerText || el.value || el.name || el.id || el.tagName)
  }
  const look = (el) => {
    const s = getComputedStyle(el)
    return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderColor, s.backgroundColor, s.color,
            s.textDecorationLine].join('|')
  }
  if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur()
  const sel = 'input:not([type=hidden]), select, textarea, button, a[href], [tabindex], [contenteditable=""], [contenteditable=true], ' +
    '[role=button], [role=link], [role=checkbox], [role=radio], [role=switch], [role=tab], [role=menuitem], [role=combobox], [onclick]'
  return [...document.querySelectorAll(sel)].filter(visible).map((el, k) => {
    el.setAttribute('data-mirmera-kb', String(k))
    const tag = el.tagName.toLowerCase()
    const native = ['input', 'select', 'textarea', 'button'].includes(tag) ? !el.disabled : (tag === 'a' && el.hasAttribute('href'))
    const ti = el.getAttribute('tabindex')
    return { k, tag, type: (el.type || '').toLowerCase(), role: el.getAttribute('role') || '', label: labelOf(el).slice(0, 80),
             tabbable: ti !== null ? Number(ti) >= 0 : (native || el.isContentEditable), disabled: !!el.disabled,
             custom: !native && (!!el.getAttribute('role') || el.hasAttribute('onclick')), look: look(el) }
  })
}"""

# Where focus is now, and how that element looks.
STOP_JS = """() => {
  const el = document.activeElement
  if (!el || el === document.body || el === document.documentElement) return null
  const s = getComputedStyle(el)
  return { k: el.getAttribute('data-mirmera-kb'), tag: el.tagName.toLowerCase(),
           look: [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderColor, s.backgroundColor, s.color,
                  s.textDecorationLine].join('|') }
}"""

UNMARK_JS = "() => document.querySelectorAll('[data-mirmera-kb]').forEach((e) => e.removeAttribute('data-mirmera-kb'))"


def _shows_focus(unfocused: str, focused: str) -> bool:
    """Something about the element changed when it got focus, and an outline isn't zero-width or 'none'."""
    if unfocused == focused:
        return False
    a, b = unfocused.split("|"), focused.split("|")
    outline_on = b[0] not in ("none", "hidden") and not b[1].startswith("0")
    return outline_on or any(x != y for x, y in zip(a[3:], b[3:]))


async def walk(page) -> dict:
    """Tabs through the page from the top. {"marks", "stops" (labels in tab order), "unreachable",
    "no_focus", "trap"}: what a keyboard user meets."""
    marks = await page.evaluate(MARK_JS)
    by_k = {str(m["k"]): m for m in marks}
    stops, seen, no_focus, trap, last, same = [], set(), [], None, None, 0
    for _ in range(min(MAX_TABS, len(marks) * 4 + 8)):
        await page.keyboard.press("Tab")
        at = await page.evaluate(STOP_JS)
        if not at:
            if stops:
                break  # focus left the page: the walk is done
            continue
        k = at["k"]
        if k == last:
            same += 1
            if same >= 6:  # a date field's day, month and year take a Tab each: a trap holds on much longer
                trap = by_k[k]["label"] if k in by_k else at["tag"]
                break
            continue
        same, last = 0, k
        if k is None or k not in by_k:
            continue  # something Mirmera didn't mark (an iframe, a shadow root): left alone
        if k in seen:
            break  # back at the top: every stop has been seen
        seen.add(k)
        m = by_k[k]
        stops.append(m["label"])
        if not _shows_focus(m["look"], at["look"]):
            no_focus.append(m["label"])
    unreachable = [m["label"] for m in marks if str(m["k"]) not in seen and not m["disabled"]
                   and (m["tag"] in ("input", "select", "textarea", "button") or m["custom"])]
    try:
        await page.evaluate(UNMARK_JS)
    except Exception:
        pass
    return {"marks": marks, "stops": stops, "unreachable": unreachable, "no_focus": no_focus, "trap": trap}


async def check_symptom(page, value: str) -> tuple[bool, str]:
    """A keyboard symptom checked again: "unreachable:<label>", "no-focus:<label>" or "trap:<label>"."""
    what, _, label = value.partition(":")
    w = await walk(page)
    if what == "unreachable":
        ok = label in w["unreachable"]
        return ok, (f'"{label}" was not reached with Tab ({len(w["stops"])} stops)' if ok
                    else f'"{label}" was reached with Tab')
    if what == "no-focus":
        ok = label in w["no_focus"]
        return ok, f'"{label}" shows no focus when tabbed to' if ok else f'"{label}" shows focus (or wasn\'t reached)'
    if what == "trap":
        ok = w["trap"] == label
        return ok, f'Tab stays on "{label}"' if ok else "Tab moves on"
    return False, f"unknown keyboard check {what}"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


async def keyboard_tour(page, signals, form_url: str, sent, to_here: list[dict], keep, *, fill) -> tuple[str, int]:
    """The form with the keyboard only. fill(page) fills the form as it was sent and returns the
    recorded steps. Returns (what it did, drafts)."""
    from urllib.parse import urlsplit
    from app import schemas
    path = urlsplit(form_url).path or "/"
    found, said = 0, []
    rec = to_here[:1] + [{"type": "goto", "url": form_url}]

    async def draft(problem, key) -> None:
        nonlocal found
        problem.same_as = f"keyboard:{path}:{key}"
        if await keep(problem, ("keyboard", key), (page, signals, list(rec))):
            found += 1

    # 1. Tab through the form page.
    await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
    await signals.settle()
    w = await walk(page)
    if not w["marks"]:
        return "nothing on the page takes the keyboard", 0
    said.append(f"{len(w['stops'])} Tab stop{'s' if len(w['stops']) != 1 else ''}")
    for label in w["unreachable"][:4]:
        is_submit = _norm(label) == _norm(sent.submit)
        await draft(schemas.ExploreProblem(
            title=f'"{label}" can\'t be reached with the keyboard'[:200], severity="high" if is_submit else "medium",
            expected=f'Tab reaches "{label}", so a user without a mouse can use it (WCAG 2.1.1 Keyboard).',
            actual=f'Tabbing through {path} visits {len(w["stops"])} stops and never "{label}". '
                   f'Tab order: {" → ".join(w["stops"][:8]) or "(nothing)"}.'[:600],
            symptom=schemas.Symptom(kind="keyboard", value=f"unreachable:{label}"[:300],
                                    explanation="Not in the Tab order")), f"unreachable:{_norm(label)}")
    if w["no_focus"]:
        first = w["no_focus"][0]
        more = len(w["no_focus"]) - 1
        await draft(schemas.ExploreProblem(
            title=(f'No visible focus on "{first}"' + (f" and {more} more" if more else ""))[:200], severity="medium",
            expected="The element with keyboard focus shows it (an outline or a change), so the user knows where they are "
                     "(WCAG 2.4.7 Focus Visible).",
            actual=f'Tabbing to {", ".join(chr(34) + x + chr(34) for x in w["no_focus"][:5])} changes nothing on screen.'[:600],
            symptom=schemas.Symptom(kind="keyboard", value=f"no-focus:{first}"[:300], explanation="Focus not shown")),
            f"no-focus:{_norm(first)}")
    if w["trap"]:
        await draft(schemas.ExploreProblem(
            title=f'Keyboard focus is stuck on "{w["trap"]}"'[:200], severity="high",
            expected="Tab always moves on (WCAG 2.1.2 No Keyboard Trap).",
            actual=f'After reaching "{w["trap"]}", pressing Tab keeps focus there.',
            symptom=schemas.Symptom(kind="keyboard", value=f"trap:{w['trap']}"[:300], explanation="A keyboard trap")),
            f"trap:{_norm(w['trap'])}")
        said.append(f'stuck on "{w["trap"]}"')

    # 2. Send the form from the keyboard, after the mouse: what the mouse got is what Enter should get.
    if sent.submit:
        from app.agent.explore import form_state, outcome_of
        mouse = None
        for attempt in ("mouse", "keyboard"):
            await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
            await signals.settle()
            steps = await fill(page)
            before = await form_state(page)
            how = None
            reachable = _norm(sent.submit) in {_norm(x) for x in w["stops"]}
            if attempt == "mouse":
                await page.get_by_role("button", name=sent.submit, exact=True).first.click(timeout=5_000)
            elif reachable:
                await page.get_by_role("button", name=sent.submit, exact=True).first.focus()
                await page.keyboard.press("Enter")
                how = {"action": "press", "key": "Enter", "locator": {"method": "role", "role": "button", "name": sent.submit}}
            else:  # the button can't be reached: Enter in a field sends a form that has a submit button
                text = next((f for f in before.fields if f["type"] in ("text", "email", "tel", "search", "number")), None)
                if not text:
                    break
                await page.get_by_label(text["label"], exact=True).first.press("Enter")
                how = {"action": "press", "key": "Enter", "locator": {"method": "label", "value": text["label"]}}
            await signals.settle(timeout_ms=5_000)
            await page.wait_for_timeout(500)
            after = await form_state(page)
            new = [m for m in after.messages if m not in before.messages] or \
                [l for l in after.lines if l not in before.lines][:3]
            moved = after.url.split("#")[0] != before.url.split("#")[0]
            if attempt == "mouse":
                if not (moved or (new and outcome_of(new, []) == "accepted")):
                    break  # the app didn't take the form again (e.g. already used data): nothing to compare
                mouse = new[0] if new else (after.heading or after.title)
                continue
            got = moved or any(_norm(mouse) == _norm(x) for x in new)
            if got:
                said.append("sent with Enter: the same answer as the mouse")
            else:
                rec.extend(steps + [{"type": "action", "action": how, "url": form_url}])
                where = f'on "{sent.submit}"' if reachable else "in a field"
                await draft(schemas.ExploreProblem(
                    title=f"The form can't be sent with the keyboard (Enter {where})"[:200], severity="high",
                    expected=f'Pressing Enter {where} sends the form, as clicking "{sent.submit}" does: '
                             f'the page says "{mouse[:150]}".'[:600],
                    actual=f'After Enter {where}, nothing was sent: the page doesn\'t say "{mouse[:150]}".'[:600],
                    symptom=schemas.Symptom(kind="text_absent", value=mouse[:300],
                                            explanation="The mouse's answer doesn't come with the keyboard")),
                    "send")
                said.append("Enter doesn't send it")
    return "the keyboard only (" + ", ".join(said) + ")", found
