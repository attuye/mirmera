"""The phone layout check: a page seen at 390 px wide, as most users here see it.

Long Amharic labels and Birr amounts break layouts that look fine in English on a laptop. The first
time a run meets a page, Mirmera narrows the browser to a phone for a moment and looks, without AI:
- the page scrolls sideways (something is wider than the screen);
- text cut off by its box (it overflows and is hidden, or ends in "…" with more behind it);
- buttons, links or fields that overlap, so one covers the other;
- tap targets smaller than 24 × 24 px (WCAG 2.5.8).
Each finding is a draft whose symptom ("layout:scroll", "layout:cut:Verify your documents") is
checked again in a fresh browser at the same width.
"""
PHONE = {"width": 390, "height": 844}
MIN_TARGET = 24

LAYOUT_JS = """(minTarget) => {
  const text = (el) => (el.getAttribute('aria-label') || el.innerText || el.value || '').replace(/\\s+/g, ' ').trim()
  const visible = (el) => {
    if (el.closest('[hidden], [aria-hidden="true"]')) return false
    const s = getComputedStyle(el), r = el.getBoundingClientRect()
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0' && r.width > 0 && r.height > 0
  }
  const out = { scroll: null, cut: [], overlap: [], small: [] }
  const doc = document.documentElement
  if (doc.scrollWidth > window.innerWidth + 2) {
    // The widest thing that sticks out on the right: what makes the page scroll sideways.
    let worst = null
    for (const el of document.body.querySelectorAll('*')) {
      if (!visible(el)) continue
      const r = el.getBoundingClientRect()
      if (r.right > window.innerWidth + 2 && (!worst || r.right > worst.r)) worst = { r: r.right, el }
    }
    out.scroll = { width: doc.scrollWidth, what: worst ? (text(worst.el).slice(0, 60) || worst.el.tagName.toLowerCase()) : '' }
  }
  for (const el of document.body.querySelectorAll('h1, h2, h3, p, li, label, button, a, td, th, span, div')) {
    if (!visible(el) || el.children.length > 0) continue  // the element that holds the words
    const t = text(el)
    if (t.length < 2) continue
    const s = getComputedStyle(el)
    const hides = ['hidden', 'clip'].includes(s.overflowX) || ['hidden', 'clip'].includes(s.overflow) || s.textOverflow === 'ellipsis'
    if (hides && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 2)) out.cut.push(t.slice(0, 60))
  }
  const targets = [...document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=button]')].filter(visible)
  const boxes = targets.map((el) => ({ el, r: el.getBoundingClientRect(), t: text(el) || el.getAttribute('placeholder') || el.name || el.tagName.toLowerCase() }))
  // WCAG 2.5.8's spacing exception: a small target passes when a 24 px circle on its centre touches
  // no other target (nor the circle of another small one).
  const centre = (r) => [r.left + r.width / 2, r.top + r.height / 2]
  const tooSmall = (x) => x.r.width < minTarget || x.r.height < minTarget
  const crowded = (a, all) => {
    const [ax, ay] = centre(a.r)
    return all.some((b) => {
      if (b === a || a.el.contains(b.el) || b.el.contains(a.el)) return false
      if (tooSmall(b)) { const [bx, by] = centre(b.r); return Math.hypot(ax - bx, ay - by) < minTarget }
      const dx = Math.max(b.r.left - ax, 0, ax - b.r.right), dy = Math.max(b.r.top - ay, 0, ay - b.r.bottom)
      return Math.hypot(dx, dy) < minTarget / 2
    })
  }
  for (let i = 0; i < boxes.length; i++) {
    const a = boxes[i]
    const inline = a.el.tagName === 'A' && a.el.closest('p, li, td')  // a link inside a sentence is exempt (WCAG 2.5.8)
    const field = ['INPUT', 'SELECT', 'TEXTAREA'].includes(a.el.tagName)  // the browser's own field sizes: not flagged
    if (!inline && !field && (a.r.width < minTarget || a.r.height < minTarget) && crowded(a, boxes)) out.small.push({ t: a.t.slice(0, 40), w: Math.round(a.r.width), h: Math.round(a.r.height) })
    for (let j = i + 1; j < boxes.length; j++) {
      const b = boxes[j]
      if (a.el.contains(b.el) || b.el.contains(a.el) || (a.el.labels && [...a.el.labels].includes(b.el))) continue
      const w = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left)
      const h = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top)
      if (w > 4 && h > 4) out.overlap.push([a.t.slice(0, 40), b.t.slice(0, 40)])
    }
  }
  out.cut = [...new Set(out.cut)].slice(0, 5)
  out.overlap = out.overlap.slice(0, 5)
  out.small = out.small.sort((x, y) => x.w * x.h - y.w * y.h).slice(0, 5)  // the smallest first
  return out
}"""


async def look(page) -> dict:
    """The page at phone width: what is wrong with its layout there. The page's own size comes back after."""
    was = page.viewport_size
    try:
        if was != PHONE:
            await page.set_viewport_size(PHONE)
            await page.wait_for_timeout(300)  # let the layout settle (media queries, resize handlers)
        return await page.evaluate(LAYOUT_JS, MIN_TARGET)
    finally:
        if was and was != PHONE:
            await page.set_viewport_size(was)


def problems(found: dict, path: str) -> list:
    """Bug drafts for what the phone layout check found on this page."""
    from app import schemas
    out = []

    def p(title, severity, expected, actual, value, why):
        out.append(schemas.ExploreProblem(
            title=title[:200], severity=severity, expected=expected, actual=actual[:600],
            symptom=schemas.Symptom(kind="layout", value=value[:300], explanation=why)))
    if found.get("scroll"):
        s = found["scroll"]
        p(f"On a phone, {path} scrolls sideways" + (f' ("{s["what"]}" is too wide)' if s["what"] else ""), "medium",
          "At 390 px wide the page fits the screen: nothing makes it scroll sideways.",
          f'At 390 px the page is {s["width"]} px wide' + (f'; the widest thing is "{s["what"]}"' if s["what"] else "") + ".",
          "scroll", "The page is wider than a phone screen")
    for t in found.get("cut", [])[:2]:
        p(f'On a phone, "{t[:50]}" is cut off on {path}', "medium",
          "Every word shows in full on a phone, or wraps to the next line.",
          f'At 390 px, "{t}" doesn\'t fit its box and the rest is hidden.', f"cut:{t}", "Text cut off by its box")
    for a, b in found.get("overlap", [])[:2]:
        p(f'On a phone, "{a[:40]}" and "{b[:40]}" overlap on {path}', "high",
          "Buttons, links and fields don't cover each other: each can be tapped.",
          f'At 390 px, "{a}" and "{b}" are drawn over each other.', f"overlap:{a}|{b}", "Two controls overlap")
    small = found.get("small", [])
    if small:
        first = small[0]
        p(f'On a phone, "{first["t"]}" is too small to tap ({first["w"]} × {first["h"]} px)'
          + (f" and {len(small) - 1} more" if len(small) > 1 else "") + f" on {path}", "low",
          f"Tap targets are at least {MIN_TARGET} × {MIN_TARGET} px (WCAG 2.5.8).",
          "At 390 px: " + "; ".join(f'"{s["t"]}" {s["w"]} × {s["h"]} px' for s in small[:5]) + ".",
          f"small:{first['t']}", "A tap target smaller than 24 px")
    return out


async def check_symptom(page, value: str) -> tuple[bool, str]:
    """A layout symptom checked again at phone width."""
    what, _, rest = value.partition(":")
    found = await look(page)
    if what == "scroll":
        ok = bool(found.get("scroll"))
        return ok, (f'at 390 px the page is {found["scroll"]["width"]} px wide' if ok else "the page fits a phone")
    if what == "cut":
        ok = rest in found.get("cut", [])
        return ok, f'"{rest}" is cut off at 390 px' if ok else f'"{rest}" shows in full'
    if what == "overlap":
        a, _, b = rest.partition("|")
        ok = [a, b] in [list(x) for x in found.get("overlap", [])] or [b, a] in [list(x) for x in found.get("overlap", [])]
        return ok, f'"{a}" and "{b}" overlap at 390 px' if ok else "they don't overlap"
    if what == "small":
        ok = any(s["t"] == rest for s in found.get("small", []))
        return ok, f'"{rest}" is under 24 px' if ok else f'"{rest}" is big enough'
    return False, f"unknown layout check {what}"
