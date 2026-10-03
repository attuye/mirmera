"""Renders frontend/public/mirmera.svg to the PNG icons browsers, iOS and the Chrome
extension need, and copies the SVG into the extension. Run after changing the SVG:

    python scripts/render_icons.py

Uses the backend's Playwright Chromium (pip install playwright; playwright install chromium).
"""
import asyncio
import os
import pathlib
import shutil

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
SVG = ROOT / "frontend" / "public" / "mirmera.svg"
TARGETS = {
    ROOT / "frontend" / "public" / "favicon-32.png": 32,
    ROOT / "frontend" / "public" / "apple-touch-icon.png": 180,
    **{ROOT / "extension" / "icons" / f"icon{n}.png": n for n in (16, 32, 48, 128)},
}


async def main() -> None:
    svg = SVG.read_text(encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM_PATH") or None)
        for path, size in TARGETS.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            page = await browser.new_page(viewport={"width": size, "height": size})
            await page.set_content(f"<style>html,body{{margin:0;background:transparent}}"
                                   f"svg{{width:{size}px;height:{size}px;display:block}}</style>{svg}")
            await page.screenshot(path=str(path), omit_background=True)
            print(f"{path.relative_to(ROOT)} ({size}px)")
        await browser.close()
    shutil.copyfile(SVG, ROOT / "extension" / "icons" / "mirmera.svg")
    print("extension/icons/mirmera.svg (copy)")


asyncio.run(main())
