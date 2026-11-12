"""SQLite storage: books, stock history, and settings."""

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("DWAB_DB", Path(__file__).resolve().parent.parent / "data" / "dwab.db"))

BOOK_FIELDS = [
    "isbn13", "work_key", "title", "authors", "published", "page_count",
    "cover_url", "category", "subgenre", "age_group", "description",
    "source_summary", "notes",
]

DEFAULT_CATEGORIES = [
    "Literary Fiction", "General Fiction", "Historical Fiction", "Mystery",
    "Thriller & Suspense", "Romance", "Romantasy", "Fantasy", "Science Fiction",
    "Horror", "Dystopian", "Adventure", "Humor", "Classics", "Graphic Novel",
    "Poetry", "True Crime", "Biography & Memoir", "History",
    "Science & Nature", "Self-Help", "Non-Fiction (General)",
]

AGE_GROUPS = ["Young Child", "Ages 8–12", "Teen 14+", "Adult"]

DEFAULT_LABEL_SETTINGS = {
    "preset": "avery5163",
    "page": "letter",  # "letter" = sheet of labels; "label" = page is one label (thermal printers)
    "width_in": 4.0, "height_in": 2.0,
    "columns": 2, "rows": 5,
    "margin_top_in": 0.5, "margin_left_in": 0.156,
    "gap_x_in": 0.188, "gap_y_in": 0.0,
    "font_pt": 14,  # maximum; text is fitted to each label up to this size
    "show_category": True, "show_subgenre": True, "show_age": True,
    "show_description": True, "show_code": True,
    "show_border": False,
    "header_text": "",
    "skip": 0,  # blank positions to skip at the start of a partly used sheet
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with session() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inventory_code TEXT UNIQUE,
                isbn13 TEXT,
                work_key TEXT,
                title TEXT NOT NULL,
                authors TEXT,
                published TEXT,
                page_count INTEGER,
                cover_url TEXT,
                category TEXT,
                subgenre TEXT,
                age_group TEXT,
                description TEXT,
                source_summary TEXT,
                notes TEXT,
                quantity INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS books_isbn ON books(isbn13);
            CREATE INDEX IF NOT EXISTS books_work ON books(work_key);
            CREATE TABLE IF NOT EXISTS stock_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                change INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        # migration: "first published" became "publication date" (2026-11)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(books)")}
        if "first_published" in cols and "published" not in cols:
            c.execute("ALTER TABLE books RENAME COLUMN first_published TO published")


# ---------- settings ----------

def get_setting(key: str, default):
    with session() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    value = json.loads(row["value"])
    if isinstance(default, dict):
        return {**default, **value}
    return value


def set_setting(key: str, value) -> None:
    with session() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def categories() -> list[str]:
    return get_setting("categories", DEFAULT_CATEGORIES)


def label_settings() -> dict:
    return get_setting("label_settings", DEFAULT_LABEL_SETTINGS)


# ---------- books ----------

def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_existing(isbn13: str | None, work_key: str | None, title: str, authors: str | None) -> dict | None:
    """Find a book already in inventory: same ISBN, same work, or same title+author."""
    with session() as c:
        if isbn13:
            row = c.execute("SELECT * FROM books WHERE isbn13 = ?", (isbn13,)).fetchone()
            if row:
                return dict(row)
        if work_key:
            row = c.execute("SELECT * FROM books WHERE work_key = ?", (work_key,)).fetchone()
            if row:
                return dict(row)
        target = (_norm(title), _norm(authors))
        for row in c.execute("SELECT * FROM books"):
            if (_norm(row["title"]), _norm(row["authors"])) == target:
                return dict(row)
    return None


def create_book(data: dict, quantity: int = 1) -> dict:
    ts = now()
    values = {f: data.get(f) for f in BOOK_FIELDS}
    with session() as c:
        cols = ", ".join(BOOK_FIELDS)
        marks = ", ".join("?" for _ in BOOK_FIELDS)
        cur = c.execute(
            f"INSERT INTO books ({cols}, quantity, created_at, updated_at) VALUES ({marks}, ?, ?, ?)",
            [*values.values(), quantity, ts, ts],
        )
        book_id = cur.lastrowid
        c.execute("UPDATE books SET inventory_code = ? WHERE id = ?", (f"DWB-{book_id:04d}", book_id))
        c.execute(
            "INSERT INTO stock_events(book_id, change, note, created_at) VALUES (?, ?, ?, ?)",
            (book_id, quantity, "initial entry", ts),
        )
    return get_book(book_id)


def get_book(book_id: int) -> dict | None:
    with session() as c:
        row = c.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return dict(row) if row else None


def update_book(book_id: int, data: dict) -> dict | None:
    fields = {f: data[f] for f in BOOK_FIELDS if f in data}
    if fields:
        assignments = ", ".join(f"{f} = ?" for f in fields)
        with session() as c:
            c.execute(
                f"UPDATE books SET {assignments}, updated_at = ? WHERE id = ?",
                [*fields.values(), now(), book_id],
            )
    return get_book(book_id)


def adjust_quantity(book_id: int, change: int, note: str | None = None) -> dict | None:
    ts = now()
    with session() as c:
        row = c.execute("SELECT quantity FROM books WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            return None
        new_qty = max(0, row["quantity"] + change)
        actual = new_qty - row["quantity"]
        if actual:
            c.execute("UPDATE books SET quantity = ?, updated_at = ? WHERE id = ?", (new_qty, ts, book_id))
            c.execute(
                "INSERT INTO stock_events(book_id, change, note, created_at) VALUES (?, ?, ?, ?)",
                (book_id, actual, note, ts),
            )
    return get_book(book_id)


def adjust_many(changes: list[tuple[int, int]], note: str | None = None) -> list[dict]:
    """Apply several quantity changes in one transaction (floors at zero)."""
    ts = now()
    ids = []
    with session() as c:
        for book_id, change in changes:
            row = c.execute("SELECT quantity FROM books WHERE id = ?", (book_id,)).fetchone()
            if row is None or not change:
                continue
            new_qty = max(0, row["quantity"] + change)
            actual = new_qty - row["quantity"]
            if actual:
                c.execute("UPDATE books SET quantity = ?, updated_at = ? WHERE id = ?", (new_qty, ts, book_id))
                c.execute(
                    "INSERT INTO stock_events(book_id, change, note, created_at) VALUES (?, ?, ?, ?)",
                    (book_id, actual, note, ts),
                )
            ids.append(book_id)
    return [get_book(i) for i in ids]


def delete_book(book_id: int) -> None:
    with session() as c:
        c.execute("DELETE FROM books WHERE id = ?", (book_id,))


def delete_books(ids: list[int]) -> int:
    """Delete several books (and their stock history) in one transaction."""
    if not ids:
        return 0
    with session() as c:
        cur = c.execute(f"DELETE FROM books WHERE id IN ({', '.join('?' for _ in ids)})", ids)
        return cur.rowcount


SORTS = {
    "created_desc": "created_at DESC, id DESC",
    "created_asc": "created_at ASC, id ASC",
    "updated_desc": "updated_at DESC, id DESC",
    "title": "title COLLATE NOCASE ASC",
    "category": "category COLLATE NOCASE ASC, title COLLATE NOCASE ASC",
    "quantity_desc": "quantity DESC, title COLLATE NOCASE ASC",
}


def list_books(sort: str = "created_desc", q: str | None = None, ids: list[int] | None = None) -> list[dict]:
    order = SORTS.get(sort, SORTS["created_desc"])
    sql, params = "SELECT * FROM books", []
    where = []
    if q:
        like = f"%{q}%"
        where.append(
            "(title LIKE ? OR authors LIKE ? OR isbn13 LIKE ? OR inventory_code LIKE ? "
            "OR category LIKE ? OR subgenre LIKE ?)"
        )
        params += [like] * 6
    if ids:
        where.append(f"id IN ({', '.join('?' for _ in ids)})")
        params += ids
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order}"
    with session() as c:
        return [dict(r) for r in c.execute(sql, params)]


def stock_history(book_id: int) -> list[dict]:
    with session() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT change, note, created_at FROM stock_events WHERE book_id = ? ORDER BY id DESC",
                (book_id,),
            )
        ]
