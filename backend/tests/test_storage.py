"""What the app keeps in the browser: changes per step with values masked, and curios worth a look."""
from app.agent import storage

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiIsIm5hbWUiOiJBYmViZSJ9.c2lnbmF0dXJlLXNpZ25hdHVyZQ"
DRAFT = '{"fullName": "Abebe Kebede", "dateOfBirth": "1990-01-01", "documentNumber": "EP1234567"}'


def snap(local=None, session=None, cookies=None):
    return {"local": local or {}, "session": session or {}, "cookies": {c["name"]: c for c in cookies or []}}


def test_what_a_value_looks_like():
    assert storage.looks_like("auth", JWT) == ["a token (JWT)"]
    assert storage.looks_like("kyc_draft", DRAFT) == ["personal details (dateOfBirth, documentNumber, fullName)"]
    assert storage.looks_like("contact", "call 0911 223 344") == ["personal details (an email or phone number)"]
    assert storage.looks_like("form", '{"email": "a@b.co", "password": "hunter2"}')[0] == "a password or PIN"
    assert storage.looks_like("theme", "dark") == [] and storage.looks_like("lang", "am") == []


def test_changes_mask_what_they_warn_about():
    before = snap(local={"theme": "light"})
    after = snap(local={"theme": "dark", "auth": JWT, "kyc_draft": DRAFT}, session={"step": "2"})
    changed = storage.changes(before, after)
    assert "~ local: theme = dark" in changed
    assert f"+ local: auth = ••• ({len(JWT)} characters)" in changed
    assert f"+ local: kyc_draft = ••• ({len(DRAFT)} characters)" in changed
    assert "+ session: step = 2" in changed
    assert "Abebe" not in " ".join(changed) and JWT not in " ".join(changed)
    assert storage.changes(after, snap(local={"theme": "dark"})) == ["- local: auth", "- local: kyc_draft", "- session: step"]


def test_curios_in_storage_and_cookies():
    now = snap(local={"auth": JWT, "kyc_draft": DRAFT, "theme": "dark"},
               cookies=[{"name": "session_id", "value": "x" * 32, "httpOnly": False, "secure": False},
                        {"name": "prefs", "value": "a", "httpOnly": False, "secure": False}])
    found = [c["what"] for c in storage.curios(now, secure=True)]
    assert 'local storage (kept after the browser closes) holds a token (JWT) under "auth"' in found
    assert 'local storage (kept after the browser closes) holds personal details (dateOfBirth, documentNumber, fullName) under "kyc_draft"' in found
    assert 'the session cookie "session_id" has no HttpOnly or Secure (any script on the page can read it)' in found
    assert not [w for w in found if "prefs" in w or "theme" in w]
    ok = snap(cookies=[{"name": "session_id", "value": "x", "httpOnly": True, "secure": False}])
    assert storage.curios(ok, secure=False) == []  # Secure only matters on https


def test_what_signing_out_leaves_behind():
    before = snap(local={"auth": JWT, "kyc_draft": DRAFT}, cookies=[{"name": "sid", "value": "abc", "httpOnly": True, "secure": True}])
    after = snap(local={"kyc_draft": DRAFT}, cookies=[{"name": "sid", "value": "abc", "httpOnly": True, "secure": True}])
    found = [c["what"] for c in storage.after_sign_out(before, after)]
    assert found == ['after signing out, local storage still holds personal details (dateOfBirth, documentNumber, fullName) under "kyc_draft"',
                     'after signing out, the session cookie "sid" is still set']
    assert storage.after_sign_out(before, snap()) == []
    assert storage.is_sign_out("click button 'Sign out'", "http://kyc.local/home")
    assert storage.is_sign_out("goto", "http://kyc.local/logout") and not storage.is_sign_out("click 'Sign in'", "/login")
