"""The phone layout check: each new page seen at 390 px wide, without AI."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_agent_eval import explore_once, practice_app, step  # noqa: F401
from tests.test_runner import chromium_available  # noqa: F401  (fixture)

CRAMPED = """<!doctype html><html lang="am"><meta name="viewport" content="width=device-width"><title>Verify</title>
<style>
  body { font-family: sans-serif; margin: 12px; position: relative }
  table { min-width: 620px; border-collapse: collapse }           /* wider than a phone */
  .go { width: 150px; white-space: nowrap; overflow: hidden }      /* a long Amharic label in a fixed box */
  .x { width: 16px; height: 16px; padding: 0; font-size: 10px }   /* too small to tap */
  .wrap { position: relative }
  .help { position: absolute; top: 4px; left: 30px }                /* lands on the button on a phone */
  @media (min-width: 700px) { .help { position: static } }
</style>
<h1>Verify your documents</h1>
<table><tr><td>Document</td><td>Status</td><td>Uploaded</td><td>Expires</td></tr></table>
<form id=f><label for=n>Full name</label><input id=n>
<div class=wrap><button type=submit class=go>ሰነዶችዎን ያረጋግጡ — Verify your documents now</button>
<a class=help href="/help">Help</a></div></form>
<div><button type=button class=x aria-label="Close">×</button><button type=button class=x aria-label="Minimise">–</button></div>
<p role=status id=out></p>
<script>document.getElementById('f').addEventListener('submit', (e) => { e.preventDefault(); out.textContent = 'Saved.' })</script>
</html>"""


@pytest.fixture
def cramped_app():
    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(CRAMPED.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.mark.layout
@pytest.mark.confirm
def test_a_page_too_wide_for_a_phone_is_drafted_and_confirmed(client, cramped_app, monkeypatch, chromium_available):  # noqa: F811
    steps, bugs = explore_once(monkeypatch, cramped_app, [
        step("Open", kind="goto", url=cramped_app),
        step("Done", kind="done", summary="Looked.")])
    titles = [b.title for b in bugs]
    assert titles[0].startswith("On a phone, / scrolls sideways"), titles
    assert titles[1:] == [
        'On a phone, "ሰነዶችዎን ያረጋግጡ — Verify your documents now" is cut off on /',
        'On a phone, "ሰነዶችዎን ያረጋግጡ — Verify your documents now" and "Help" overlap on /',
        'On a phone, "Close" is too small to tap (16 × 16 px) and 1 more on /'], titles  # two side by side: crowded
    # checked again in a fresh browser at 390 px: every one shows again
    assert all(b.reproducibility == "always" for b in bugs), [(b.title, b.confirmation) for b in bugs]


@pytest.mark.layout
def test_a_page_that_fits_a_phone_gets_nothing(client, practice_app, monkeypatch, chromium_available):  # noqa: F811
    steps, bugs = explore_once(monkeypatch, practice_app, [
        step("Open", kind="goto", url=f"{practice_app}/verify"),
        step("Done", kind="done", summary="Looked.")])
    assert not any(b.title.startswith("On a phone") for b in bugs), [b.title for b in bugs]
