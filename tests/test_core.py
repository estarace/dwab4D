import os
import tempfile

import pytest

os.environ["DWAB_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import ai, db, main  # noqa: E402
from app.isbn import normalize  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    with db.session() as c:
        c.executescript("DROP TABLE IF EXISTS stock_events; DROP TABLE IF EXISTS books; DROP TABLE IF EXISTS settings;")
    db.init()


client = TestClient(main.app)


# ---------- ISBN ----------

@pytest.mark.parametrize("raw,expected", [
    ("978-0-553-29335-7", "9780553293357"),
    ("0553293354", "9780553293357"),         # ISBN-10 -> 13
    ("0-8044-2957-X", "9780804429573"),      # X check digit
    ("978055329335751299", "9780553293357"),  # barcode with price add-on
    ("9780553293358", None),                 # bad check digit
    ("The Night Circus", None),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# ---------- leak check ----------

BOOK = {"title": "The Night Circus", "authors": "Erin Morgenstern",
        "characters": ["Celia Bowen", "Marco Alisdair"]}


def test_leak_check_catches_title_author_characters():
    text = "Celia and her rival compete inside the night circus, a dream by Morgenstern."
    leaks = ai.find_leaks(text, BOOK)
    assert "Night Circus" in leaks
    assert "Morgenstern" in leaks
    assert "Celia" in leaks


def test_leak_check_uses_model_terms_and_passes_clean_text():
    clean = "Two young magicians are bound to a contest they never chose. The stage only opens after dark."
    assert ai.find_leaks(clean, BOOK) == []
    assert ai.find_leaks("Le Cirque des Rêves awaits.", BOOK, ["Le Cirque des Rêves"]) == ["Le Cirque des Rêves"]


def test_sentence_count():
    assert ai._sentence_count("One. Two! Three? Four… Five.") == 5
    assert ai._sentence_count("Just one sentence here.") == 1


# ---------- inventory API ----------

def _book(**kw):
    return {"title": "Foundation", "authors": "Isaac Asimov", "isbn13": "9780553293357",
            "category": "Science Fiction", "age_group": "Adult",
            "description": "An empire is dying. One man has done the math.", **kw}


def test_create_assigns_code_and_merges_duplicates():
    r = client.post("/api/books", json=_book(quantity=2))
    assert r.status_code == 200
    b = r.json()
    assert b["inventory_code"] == "DWB-0001"
    assert b["quantity"] == 2

    dup = client.post("/api/books", json=_book(isbn13=None, title="foundation", quantity=1))
    assert dup.status_code == 200
    assert dup.json()["merged"] is True
    assert dup.json()["id"] == b["id"] and dup.json()["quantity"] == 3
    assert len(client.get("/api/books").json()) == 1


def test_quantity_history_and_floor_at_zero():
    b = client.post("/api/books", json=_book()).json()
    client.post(f"/api/books/{b['id']}/quantity", json={"change": 2, "note": "added copy"})
    r = client.post(f"/api/books/{b['id']}/quantity", json={"change": -10})
    assert r.json()["quantity"] == 0
    hist = client.get(f"/api/books/{b['id']}").json()["history"]
    assert [h["change"] for h in hist] == [-3, 2, 1]


def test_sort_by_date_entered_and_csv():
    a = client.post("/api/books", json=_book()).json()
    b = client.post("/api/books", json=_book(title="Dune", authors="Frank Herbert", isbn13="9780441013593")).json()
    newest = client.get("/api/books?sort=created_desc").json()
    oldest = client.get("/api/books?sort=created_asc").json()
    assert [x["id"] for x in newest] == [b["id"], a["id"]]
    assert [x["id"] for x in oldest] == [a["id"], b["id"]]

    csv = client.get(f"/api/export.csv?ids={a['id']}").text
    lines = csv.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("Inventory Code,Title,Author")
    assert len(lines) == 2 and "Foundation" in lines[1]


def test_settings_roundtrip():
    r = client.put("/api/settings", json={"categories": ["Mystery", " Romance ", "Mystery", ""],
                                          "label_settings": {"show_code": False, "bogus": 1}})
    s = r.json()
    assert s["categories"] == ["Mystery", "Romance"]
    assert s["label_settings"]["show_code"] is False
    assert "bogus" not in s["label_settings"]
    assert s["label_settings"]["width_in"] == 4.0  # defaults preserved


def test_lookup_rejects_bad_isbn_checksum():
    r = client.post("/api/lookup", json={"query": "978-0-553-29335-8"})
    assert r.status_code == 400


# ---------- enrichment retry (AI mocked) ----------

def test_enrich_retries_when_teaser_leaks(monkeypatch):
    import asyncio
    replies = [
        {"category": "Fantasy", "subgenre": "Historical Fantasy", "age_group": "Adult",
         "description": "Celia was raised for a duel she never chose.",
         "giveaway_terms": ["Le Cirque des Rêves"]},
        {"category": "Fantasy", "subgenre": "Historical Fantasy", "age_group": "Adult",
         "description": "Two young illusionists are bound to a contest they never chose. "
                        "The gates open only at dusk. Love may be the one trick neither can afford.",
         "giveaway_terms": ["Le Cirque des Rêves"]},
    ]
    prompts = []

    async def fake_call(system, content, schema, effort="medium"):
        prompts.append(content)
        return replies[len(prompts) - 1]

    monkeypatch.setattr(ai, "_call", fake_call)
    out = asyncio.run(ai.enrich({**BOOK, "published": "2011"}, ["Fantasy"], db.AGE_GROUPS))
    assert len(prompts) == 2
    assert "Celia" in prompts[1]          # retry explains what leaked
    assert out["warnings"] == []
    assert out["description"].startswith("Two young illusionists")


def test_generate_reports_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    ai._client = None
    r = client.post("/api/generate", json={"book": {"title": "Foundation"}})
    assert r.status_code == 502
    assert "API key" in r.json()["detail"]
