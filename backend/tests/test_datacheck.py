"""Display versus data, in plain code: the page against the JSON it was drawn from, and curios in the JSON."""
from datetime import date

from app.agent.datacheck import curios, labelled, mismatches, words

TODAY = date(2026, 11, 23)


def answer(data, url="http://kyc.local/api/verification/42"):
    return {"url": url, "method": "GET", "data": data}


GARBLED = "አበበ ከበደ".encode().decode("cp1252", errors="replace")  # what a page shows reading UTF-8 as Windows-1252
DATA = {"id": 42, "status": "rejected", "fullName": "አበበ ከበደ", "expiryDate": "2026-11-30", "documentType": "PASSPORT",
        "documentNumber": "EP1234567", "fee": 150.0, "createdAt": "2026-11-20T08:00:00Z"}


def test_what_the_page_shows_is_read_as_label_and_value():
    page = "Your verification\nStatus: Verified\nExpiry date\n29/11/2026\nFee\t1,500.00 ETB\nUpload another"
    assert labelled(page)[:3] == [("Your verification", "Status: Verified"), ("Status", "Verified"), ("Expiry date", "29/11/2026")]
    assert ("Fee", "1,500.00 ETB") in labelled(page)
    assert words("expiryDate") == ["expiry", "date"] and words("date_of_birth") == ["date", "of", "birth"]
    assert words("dob") == ["date", "of", "birth"]


def test_the_page_contradicting_its_data_is_found():
    page = f"""Your verification
Status: Verified
Full name: {GARBLED}
Expiry date: 29/11/2026
Document type: Passport
Document number: EP12
Fee: 15.00 ETB"""
    found = {m["label"]: m for m in mismatches([answer(DATA)], page)}
    assert found["Status"]["why"] == "the data says negative, the page says positive"
    assert found["Expiry date"]["why"].startswith("one day earlier: a date without a time")
    assert found["Full name"]["why"].startswith("garbled")
    assert found["Document number"]["why"] == "cut short"
    assert found["Fee"]["why"].startswith("off by a factor of 10")
    assert "Document type" not in found  # PASSPORT shown as Passport is the same thing
    assert found["Status"]["key"] == "status" and found["Status"]["value"] == "rejected"
    assert found["Status"]["url"] == "http://kyc.local/api/verification/42"


def test_formatting_is_never_a_difference():
    page = """Status: REJECTED
Full name: አበበ ከበደ
Expiry date: 30 Nov 2026
Document type: Passport
Document number: EP 123 4567
Fee: 150 ETB
Created: 21/11/2026"""
    assert mismatches([answer(DATA)], page) == []
    assert mismatches([answer(DATA)], "Expiry date: 2026-11-30\nExpiry date: November 30, 2026") == []
    assert mismatches([answer({**DATA, "fee": 1500})], "Fee: 1,500.00") == []
    assert mismatches([answer({**DATA, "fee": 1500})], "Fee: 1.500,00") == []
    assert mismatches([answer({"expiresAt": "2026-11-30T22:30:00Z"})], "Expires at: 01/12/2026") == []  # a time zone
    assert mismatches([answer({"status": "pending_review"})], "Status: In review") == []
    assert mismatches([answer({"status": "approved"})], "Status: Approved ✓") == []


def test_a_page_only_counts_when_its_label_names_the_field():
    """"Date" alone is not the expiry date; a list of many records is a table, not one thing."""
    assert mismatches([answer({"expiryDate": "2026-11-30"})], "Date: 01/01/2020") == []
    many = [{"status": "rejected"}, {"status": "approved"}]
    assert mismatches([answer(many)], "Status: Verified") == []
    assert mismatches([answer([{"status": "rejected"}])], "Status: Verified")  # one record is one thing


def test_curious_things_inside_the_data():
    apps = [{"id": i, "status": "verified", "expiryDate": "2027-01-01"} for i in range(1, 11)]
    apps[2]["expiryDate"] = "2025-01-01"  # verified, but expired
    apps[5]["expiryDate"] = None
    apps[7]["id"] = 2  # the same id twice
    person = {"dateOfBirth": "2030-01-01", "issueDate": "2026-05-01", "expiryDate": "2026-01-01"}
    found = [c["what"] for c in curios([answer(apps, "http://kyc.local/api/applications"), answer(person)], TODAY)]
    assert '[2]: status "verified" but expiryDate 2025-01-01 has passed' in found
    assert "1 of 10 records in (the answer) have no expiryDate" in found
    assert "the same id twice in (the answer): 2" in found
    assert "the answer: dateOfBirth 2030-01-01 is in the future" in found
    assert "the answer: issueDate 2026-05-01 is after expiryDate 2026-01-01" in found


def test_nothing_curious_in_ordinary_data():
    apps = [{"id": i, "status": "verified", "expiryDate": "2027-01-01", "note": None} for i in range(10)]
    assert curios([answer(apps), answer({"status": "rejected", "expiryDate": "2020-01-01"})], TODAY) == []
