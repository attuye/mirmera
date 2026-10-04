// Mirmera's accessibility check: run once when a page has loaded, in the page.
// A small, explainable subset of WCAG checks that are reliable without a human:
// what it finds is a lead for the tester, never a verdict. Returns
// [{rule, text}] with at most LIMIT findings per rule.
(() => {
  const LIMIT = 5;
  const out = [];
  const counts = {};
  const add = (rule, text) => {
    counts[rule] = (counts[rule] || 0) + 1;
    if (counts[rule] <= LIMIT) out.push({ rule, text });
  };
  const visible = (el) => {
    if (el.closest('[aria-hidden="true"], [hidden]')) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const clip = (t, n = 40) => { t = (t || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
  const describe = (el) => {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    const name = el.getAttribute('name');
    if (name) return `${tag}[name="${name}"]`;
    if (tag === 'img' && el.getAttribute('src')) return `img ${clip(el.getAttribute('src').split('/').pop(), 40)}`;
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/)[0];
    if (cls) return `${tag}.${cls}`;
    const text = clip(el.textContent, 30);
    return text ? `${tag} "${text}"` : tag;
  };
  const labelled = (el) => {
    if ((el.getAttribute('aria-label') || '').trim()) return true;
    const by = el.getAttribute('aria-labelledby');
    if (by && by.split(/\s+/).some((id) => (document.getElementById(id)?.textContent || '').trim())) return true;
    if (el.id && [...document.querySelectorAll('label[for]')].some((l) => l.htmlFor === el.id && l.textContent.trim())) return true;
    if (el.closest('label') && el.closest('label').textContent.trim()) return true;
    return !!(el.getAttribute('title') || '').trim();
  };
  const named = (el) => labelled(el) || !!el.textContent.trim() ||
    [...el.querySelectorAll('img[alt]')].some((i) => i.getAttribute('alt').trim()) ||
    (el.tagName === 'INPUT' && !!(el.value || '').trim());

  if (!(document.documentElement.getAttribute('lang') || '').trim()) add('lang', 'Page has no language (<html lang>)');
  if (!document.title.trim()) add('title', 'Page has no title');

  for (const img of document.querySelectorAll('img')) {
    if (!visible(img) || img.hasAttribute('alt') || img.getAttribute('role') === 'presentation') continue;
    add('img-alt', `Image without alt text: ${describe(img)}`);
  }
  const fields = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image]), select, textarea';
  for (const el of document.querySelectorAll(fields)) {
    if (visible(el) && !labelled(el)) add('label', `Form field without a label: ${describe(el)}`);
  }
  for (const el of document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')) {
    if (visible(el) && !named(el)) add('button-name', `Button without a name: ${describe(el)}`);
  }
  for (const el of document.querySelectorAll('a[href]')) {
    if (visible(el) && !named(el)) add('link-name', `Link without a name: ${describe(el)}`);
  }
  const ids = {};
  for (const el of document.querySelectorAll('[id]')) ids[el.id] = (ids[el.id] || 0) + 1;
  for (const [id, n] of Object.entries(ids)) if (n > 1) add('duplicate-id', `Duplicate id "${id}" (${n} elements)`);

  // Contrast of text against the first opaque background behind it (WCAG AA: 4.5:1, large text 3:1).
  const rgb = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const background = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;  // an image: can't tell
      const c = rgb(s.backgroundColor);
      if (c && c.a >= 0.99) return c;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  };
  const seen = new Set();
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own || !visible(el)) continue;
    const s = getComputedStyle(el);
    const fg = rgb(s.color), bg = background(el);
    if (!fg || !bg || fg.a < 0.99) continue;
    const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
    const ratio = (hi + 0.05) / (lo + 0.05);
    const size = parseFloat(s.fontSize), bold = Number(s.fontWeight) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    const text = clip(el.textContent, 40);
    if (ratio < need && !seen.has(text)) {
      seen.add(text);
      add('contrast', `Low contrast text (${ratio.toFixed(1)}:1, needs ${need}:1): "${text}"`);
    }
  }
  for (const [rule, n] of Object.entries(counts)) if (n > LIMIT) out.push({ rule, text: `More findings like these (${rule}): only the first ${LIMIT} are listed` });
  return out;
})()
