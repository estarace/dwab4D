"""Tests for bulk entry, label-driven stock changes, and publication dates."""

import sqlite3

import pytest

from tests.test_core import _book, client  # noqa: F401  (shares the temp DB + fixture setup)
from tests.test_core import fresh_db  # noqa: F401

from app import ai, bulk, db, lookup
from app.lookup import best_match, normalize_date


# ---------- publication date ----------

@pytest.mark.parametrize("raw,expected", [
    ("October 1, 1991", "1991-10-01"), ("Aug 24, 2012", "2012-08-24"), ("2017", "2017"),
    ("2004-10-01", "2004-10-01"), ("Sept 2009", "2009-09"), ("c1991", "1991"), ("", None), (None, None),
])
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


def test_migrates_first_published_column(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, inventory_code TEXT, isbn13 TEXT, "
        "work_key TEXT, title TEXT NOT NULL, authors TEXT, first_published TEXT, page_count INTEGER, "
        "cover_url TEXT, category TEXT, subgenre TEXT, age_group TEXT, description TEXT, "
        "source_summary TEXT, notes TEXT, quantity INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT)"
    )
    con.execute("INSERT INTO books(title, first_published, created_at, updated_at) VALUES ('Old', '1999', 'x', 'x')")
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init()
    assert db.list_books()[0]["published"] == "1999"


# ---------- labels: subtract printed copies ----------

def test_bulk_quantities_floor_at_zero_and_log():
    a = client.post("/api/books", json=_book(quantity=3)).json()
    b = client.post("/api/books", json=_book(title="Dune", authors="Frank Herbert",
                                             isbn13="9780441013593", quantity=1)).json()
    r = client.post("/api/books/quantities", json={
        "changes": [{"id": a["id"], "change": -2}, {"id": b["id"], "change": -5}],
        "note": "labels printed",
    })
    assert {x["id"]: x["quantity"] for x in r.json()} == {a["id"]: 1, b["id"]: 0}
    latest = client.get(f"/api/books/{a['id']}").json()["history"][0]
    assert latest["change"] == -2 and latest["note"] == "labels printed"


# ---------- bulk parsing ----------

def test_parse_plain_list():
    text = ("1. 978-0-553-29335-7\n- The Night Circus by Erin Morgenstern\nGone Girl x2\n\n"
            "# a comment\nGone Girl\nDune\tFrank Herbert\t3\n")
    items = bulk.parse(text)
    assert {"query": "9780553293357", "author": None, "quantity": 1} in items
    assert {"query": "The Night Circus", "author": "Erin Morgenstern", "quantity": 1} in items
    assert {"query": "Gone Girl", "author": None, "quantity": 3} in items      # repeats merged
    assert {"query": "Dune", "author": "Frank Herbert", "quantity": 3} in items
    assert len(items) == 4


def test_parse_csv_with_header():
    text = ('﻿Title,Author,ISBN,Qty\n"Eat, Pray, Love",Elizabeth Gilbert,,2\n'
            ',,0553293354,\nDune,Frank Herbert,,\n')
    assert bulk.parse(text) == [
        {"query": "Eat, Pray, Love", "author": "Elizabeth Gilbert", "quantity": 2},
        {"query": "9780553293357", "author": None, "quantity": 1},
        {"query": "Dune", "author": "Frank Herbert", "quantity": 1},
    ]


def test_parse_empty_is_400():
    assert client.post("/api/bulk/parse", json={"text": "  \n# nothing\n"}).status_code == 400


# ---------- matching ----------

def test_best_match():
    c = [{"title": "The Night Circus", "authors": "Erin Morgenstern", "edition_count": 37},
         {"title": "The night circus", "authors": "Ben Gazzara", "edition_count": 1},
         {"title": "Nights at the circus", "authors": "Angela Carter", "edition_count": 9}]
    assert best_match("night circus", None, c)["authors"] == "Erin Morgenstern"   # clearly best-known
    assert best_match("The Night Circus", "Gazzara", c)["authors"] == "Ben Gazzara"
    assert best_match("The Night Circus Erin Morgenstern", None, c)["authors"] == "Erin Morgenstern"
    close = [{"title": "Wonder", "authors": "A", "edition_count": 6},
             {"title": "Wonder", "authors": "B", "edition_count": 4}]
    assert best_match("Wonder", None, close) is None                              # ambiguous -> ask
    assert best_match("Circus", None, c) is None


# ---------- bulk item ----------

def test_bulk_item_adds_then_increments(monkeypatch):
    found = {"isbn13": "9780553293357", "work_key": "/works/OL1W", "title": "Foundation",
             "authors": "Isaac Asimov", "published": "1991-10-01", "page_count": 240, "cover_url": None,
             "source_summary": "x", "subjects": [], "characters": [], "places": []}
    calls = {"ai": 0}

    async def fake_identify(**kw):
        return dict(found)

    async def fake_enrich(book, cats, ages, **kw):
        calls["ai"] += 1
        return {"category": "Science Fiction", "subgenre": "Space Opera", "age_group": "Adult",
                "description": "An empire falls.", "warnings": []}

    monkeypatch.setattr(lookup, "identify", fake_identify)
    monkeypatch.setattr(ai, "enrich", fake_enrich)
    r1 = client.post("/api/bulk/item", json={"query": "9780553293357", "quantity": 2}).json()
    assert r1["status"] == "added"
    assert r1["book"]["quantity"] == 2 and r1["book"]["published"] == "1991-10-01"
    r2 = client.post("/api/bulk/item", json={"query": "0553293354"}).json()
    assert r2["status"] == "incremented" and r2["book"]["quantity"] == 3
    assert calls["ai"] == 1   # no AI call for a book already in stock


def test_bulk_item_asks_when_ambiguous(monkeypatch):
    async def fake_search(title, author=None, limit=8):
        return [{"title": "Wonder", "authors": "A", "edition_count": 6, "work_key": "/w/1"},
                {"title": "Wonder", "authors": "B", "edition_count": 4, "work_key": "/w/2"}]

    monkeypatch.setattr(lookup, "search", fake_search)
    r = client.post("/api/bulk/item", json={"query": "Wonder"}).json()
    assert r["status"] == "choose" and len(r["candidates"]) == 2


# ---------- bulk delete ----------

def test_bulk_delete_removes_books_and_history():
    a = client.post("/api/books", json=_book(quantity=2)).json()
    b = client.post("/api/books", json=_book(title="Dune", authors="Frank Herbert", isbn13="9780441013593")).json()
    c = client.post("/api/books", json=_book(title="Emma", authors="Jane Austen", isbn13="9780141439587")).json()
    assert [x["id"] for x in client.get(f"/api/books?ids={a['id']},{c['id']}&sort=created_asc").json()] == [a["id"], c["id"]]

    r = client.post("/api/books/delete", json={"ids": [a["id"], c["id"], a["id"], 9999]})
    assert r.json() == {"deleted": 2}
    assert [x["id"] for x in client.get("/api/books").json()] == [b["id"]]
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stock_events WHERE book_id IN (?, ?)", (a["id"], c["id"])).fetchone()[0] == 0
    # next new book never reuses a deleted inventory code
    d = client.post("/api/books", json=_book(title="Ivanhoe", authors="Walter Scott", isbn13="9780140436587")).json()
    assert d["inventory_code"] == "DWB-0004"


def test_bulk_delete_requires_ids():
    assert client.post("/api/books/delete", json={"ids": []}).status_code == 422
