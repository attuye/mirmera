"""A failed request as a curl command for the bug report, without handing out anyone's access."""
from app.curl import curl_of


def test_a_post_with_json():
    cmd = curl_of("POST", "http://localhost:4100/api/verify",
                  {"content-type": "application/json", "cookie": "session=abc123", "user-agent": "Chrome/1",
                   "sec-ch-ua": '"Chromium"', "accept": "*/*", "x-request-id": "r-1"},
                  '{"fullName": "Abebe Kebede", "password": "Hunter22!", "documentType": "Passport"}')
    assert cmd == ("curl -X POST http://localhost:4100/api/verify \\\n"
                   "  -H 'accept: */*' \\\n"
                   "  -H 'content-type: application/json' \\\n"
                   "  -H 'x-request-id: r-1' \\\n"
                   "  -H 'Cookie: <your session: copy it from your browser>' \\\n"
                   "  --data-raw '{\"fullName\": \"Abebe Kebede\", \"password\": \"•••\", \"documentType\": \"Passport\"}'")
    assert "abc123" not in cmd and "Hunter22" not in cmd and "user-agent" not in cmd


def test_a_form_body_a_get_and_known_secrets():
    form = curl_of("POST", "http://app/login", {"authorization": "Bearer xyz"}, "email=a%40b.c&user_password=s3cret&pin=1234")
    assert "user_password=•••" in form and "pin=•••" in form and "xyz" not in form and "email=a%40b.c" in form
    assert curl_of("GET", "http://app/api/documents?id=7") == "curl 'http://app/api/documents?id=7'"
    # a sign-in secret Mirmera knows is masked wherever it appears
    assert "Abebe123" not in curl_of("POST", "http://app/x", {}, '{"note": "pw is Abebe123"}', secrets=["Abebe123"])
