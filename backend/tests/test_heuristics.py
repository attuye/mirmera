"""The heuristics library, picking heuristics for a charter, and their test ideas."""
from app import heuristics, schemas
from typing import get_args


def test_the_library_matches_the_apps_own_vocabularies():
    keys = {h.group: set() for h in heuristics.LIBRARY}
    for h in heuristics.LIBRARY:
        keys[h.group].add(h.key)
        assert h.summary and len(h.questions) >= 2 and h.example
    assert keys["oracle"] == set(get_args(schemas.OracleHeuristic))  # the bug report's oracles
    assert keys["element"] == set(get_args(schemas.SFDIPOT))  # the product model's categories
    assert keys["quality"] == set(get_args(schemas.QualityCriterion))  # the risks' quality criteria
    assert len(keys["technique"]) >= 10
    assert len(heuristics.BY_KEY) == len(heuristics.LIBRARY)  # keys are unique


def test_the_catalog_and_choosing_heuristics_for_a_charter(client, project, charter):
    lib = client.get("/heuristics").json()
    assert [g["key"] for g in lib["groups"]] == ["oracle", "element", "quality", "technique"]
    assert any(h["key"] == "goldilocks" and h["group"] == "technique" for h in lib["heuristics"])

    c = client.put(f"/charters/{charter['id']}/heuristics", json={"keys": ["goldilocks", "interruptions", "goldilocks"]}).json()
    assert c["heuristics"] == ["goldilocks", "interruptions"]
    assert client.get(f"/charters/{charter['id']}").json()["heuristics"] == ["goldilocks", "interruptions"]
    r = client.put(f"/charters/{charter['id']}/heuristics", json={"keys": ["vibes"]})
    assert r.status_code == 400 and "Unknown heuristics: vibes" in r.json()["detail"]
    made = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "x", "heuristics": ["starve"]}).json()
    assert made["heuristics"] == ["starve"]


def test_chosen_heuristics_come_back_as_test_ideas_until_a_note_uses_them(client, charter, session):
    client.put(f"/charters/{charter['id']}/heuristics", json={"keys": ["goldilocks", "concurrency"]})
    ideas = {n["title"]: n for n in client.get(f"/sessions/{session['id']}/nudges").json()}
    g = ideas["Try: Goldilocks"]
    assert (g["kind"], g["heuristic"], g["heuristic_key"]) == ("heuristic", "Chosen for this charter", "goldilocks")
    assert g["suggestions"][0] == "What is the smallest and largest value allowed?"
    client.post(f"/sessions/{session['id']}/notes", json={"tag": "test", "content": "Goldilocks on the coupon field: 0, 1, 999"})
    titles = {n["title"] for n in client.get(f"/sessions/{session['id']}/nudges").json()}
    assert "Try: Goldilocks" not in titles and "Try: Concurrency" in titles
