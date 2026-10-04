"""Projects: create, list with counts, settings."""
import pytest


def make(client, **fields):
    return client.post("/projects", json={"name": "KYC", **fields})


def test_a_project_is_created_with_its_details(client):
    r = make(client, name="  AddisVerify KYC ", description="ID document checks", app_url="http://localhost:4000")
    assert r.status_code == 200
    p = r.json()
    assert (p["name"], p["description"], p["app_url"], p["org_id"]) == (
        "AddisVerify KYC", "ID document checks", "http://localhost:4000", client.me["workspaces"][0]["id"])
    assert p["environments"] == [] and p["default_tester"] is None


@pytest.mark.parametrize("fields", [{"name": "   "}, {"name": ""}, {"app_url": "localhost:4000"}, {"app_url": "javascript:alert(1)"}])
def test_bad_project_details_are_refused(client, fields):
    assert make(client, **fields).status_code == 422


def test_projects_are_listed_with_counts_most_recent_work_first(client, project, charter, session):
    quiet = make(client, name="Quiet").json()
    assert client.get("/projects").json()[0]["name"] == "Quiet"  # just created: the latest thing that happened
    client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"})  # now ShopFlow is
    client.post(f"/projects/{project['id']}/risks", json={
        "title": "Coupon stacks", "failure_mode": "Two coupons apply", "quality_criterion": "capability",
        "impact": 4, "likelihood": 3})
    client.post(f"/sessions/{session['id']}/bugs", json={"title": "Total is negative"})
    listed = client.get("/projects").json()
    assert [p["name"] for p in listed] == ["ShopFlow", "Quiet"]
    shop = listed[0]
    assert (shop["risk_count"], shop["charter_count"], shop["session_count"], shop["bug_count"]) == (1, 1, 2, 1)
    assert shop["last_activity"] is not None
    assert (listed[1]["id"], listed[1]["session_count"], listed[1]["last_activity"]) == (quiet["id"], 0, None)


def test_settings_are_updated_and_cleared(client, project):
    url = f"/projects/{project['id']}"
    r = client.patch(url, json={
        "description": "Checkout", "app_url": "https://staging.shop.test", "default_tester": " attu ",
        "environments": [{"name": "staging", "url": "https://staging.shop.test"}, {"name": "prod", "url": "https://shop.test"}]})
    assert r.status_code == 200
    p = r.json()
    assert p["default_tester"] == "attu"
    assert [e["name"] for e in p["environments"]] == ["staging", "prod"]
    assert client.get(url).json()["environments"][1] == {"name": "prod", "url": "https://shop.test"}

    p = client.patch(url, json={"app_url": "", "description": "", "default_tester": "", "environments": []}).json()
    assert (p["app_url"], p["description"], p["default_tester"], p["environments"]) == (None, None, None, [])
    assert p["name"] == "ShopFlow"  # untouched


@pytest.mark.parametrize("change", [
    {"environments": [{"name": "staging", "url": "https://a.test"}, {"name": "Staging", "url": "https://b.test"}]},
    {"environments": [{"name": "staging", "url": "ftp://a.test"}]},
    {"environments": [{"name": " ", "url": "https://a.test"}]},
    {"environments": [{"name": f"e{i}", "url": "https://a.test"} for i in range(11)]},
    {"app_url": "not a url"},
    {"name": "   "},
])
def test_bad_settings_are_refused(client, project, change):
    assert client.patch(f"/projects/{project['id']}", json=change).status_code == 422


def test_each_project_sees_only_its_own_work(client, project, charter):
    other = make(client, name="Other").json()
    assert client.get(f"/projects/{other['id']}/charters").json() == []
    assert [c["id"] for c in client.get(f"/projects/{project['id']}/charters").json()] == [charter["id"]]


def test_the_extension_sees_which_project_a_session_is_in(client, project, session):
    [active] = client.get("/sessions/active").json()
    assert (active["project_id"], active["project_name"]) == (project["id"], "ShopFlow")


def test_a_session_says_which_project_it_is_in(client, project, session):
    assert session["project_id"] == project["id"]
    assert client.get(f"/sessions/{session['id']}").json()["project_id"] == project["id"]
