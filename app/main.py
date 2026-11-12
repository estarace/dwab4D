"""FastAPI app: JSON API + static front end."""

import asyncio
import csv
import io
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, bulk, db, lookup
from .isbn import normalize

STATIC = Path(__file__).resolve().parent / "static"
MAX_PHOTO_BYTES = 8 * 1024 * 1024

app = FastAPI(title="Date with a Book")
db.init()


# ---------- models ----------

class LookupIn(BaseModel):
    query: str
    author: str | None = None


class IdentifyIn(BaseModel):
    isbn13: str | None = None
    work_key: str | None = None
    title: str | None = None
    author: str | None = None


class GenerateIn(BaseModel):
    book: dict
    nudge: str | None = None
    previous: str | None = None


class BookIn(BaseModel):
    isbn13: str | None = None
    work_key: str | None = None
    title: str = Field(min_length=1)
    authors: str | None = None
    published: str | None = None
    page_count: int | None = None
    cover_url: str | None = None
    category: str | None = None
    subgenre: str | None = None
    age_group: str | None = None
    description: str | None = None
    source_summary: str | None = None
    notes: str | None = None
    quantity: int = Field(default=1, ge=0)


class BookPatch(BaseModel):
    title: str | None = None
    authors: str | None = None
    isbn13: str | None = None
    published: str | None = None
    page_count: int | None = None
    category: str | None = None
    subgenre: str | None = None
    age_group: str | None = None
    description: str | None = None
    notes: str | None = None


class QuantityIn(BaseModel):
    change: int
    note: str | None = None


class QuantityChange(BaseModel):
    id: int
    change: int


class QuantitiesIn(BaseModel):
    changes: list[QuantityChange]
    note: str | None = None


class DeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=5000)


class BulkParseIn(BaseModel):
    text: str = Field(max_length=500_000)


class BulkItemIn(BaseModel):
    query: str = ""
    author: str | None = None
    quantity: int = Field(default=1, ge=1, le=999)
    work_key: str | None = None   # set when the user picked a specific match
    isbn13: str | None = None


class SettingsIn(BaseModel):
    categories: list[str] | None = None
    label_settings: dict | None = None


# ---------- helpers ----------

def _existing(book: dict) -> dict | None:
    return db.find_existing(book.get("isbn13"), book.get("work_key"), book.get("title") or "", book.get("authors"))


async def _identified(**kwargs) -> dict:
    try:
        book = await lookup.identify(**kwargs)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Book database lookup failed: {e}") from e
    if not book:
        raise HTTPException(404, "No matching book found. Try the title instead, or enter details by hand.")
    return {"kind": "book", "book": book, "existing": _existing(book)}


async def _search(title: str, author: str | None) -> dict:
    try:
        candidates = await lookup.search(title, author)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Book database search failed: {e}") from e
    return {"kind": "candidates", "candidates": candidates}


# ---------- lookup & AI ----------

@app.post("/api/lookup")
async def api_lookup(body: LookupIn):
    q = body.query.strip()
    if not q:
        raise HTTPException(400, "Enter a title or ISBN.")
    if isbn := normalize(q):
        return await _identified(isbn13=isbn)
    if len(q.replace("-", "").replace(" ", "")) in (10, 13) and q.replace("-", "").replace(" ", "").isdigit():
        raise HTTPException(400, "That looks like an ISBN but the check digit is wrong — please re-check it.")
    return await _search(q, body.author)


@app.post("/api/identify")
async def api_identify(body: IdentifyIn):
    isbn = normalize(body.isbn13) if body.isbn13 else None
    return await _identified(isbn13=isbn, work_key=body.work_key, title=body.title, author=body.author)


async def _read_photo(file: UploadFile) -> list[dict]:
    data = await file.read()
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo is too large (8 MB max).")
    try:
        return await ai.read_photo(data, file.content_type or "")
    except ai.AIError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/photo")
async def api_photo(file: UploadFile = File(...)):
    """Single-book flow: identify the (first) book in the photo."""
    books = await _read_photo(file)
    if not books:
        raise HTTPException(422, "Couldn't read a title or ISBN from that photo. Try a clearer shot of the cover or barcode.")
    found, others = books[0], len(books) - 1
    if found.get("isbn") and (isbn := normalize(found["isbn"])):
        try:
            return {**(await _identified(isbn13=isbn)), "read": found, "others": others}
        except HTTPException:
            pass  # fall through to title search
    if found.get("title"):
        return {**(await _search(found["title"], found.get("author"))), "read": found, "others": others}
    raise HTTPException(422, "Couldn't read a title or ISBN from that photo. Try a clearer shot of the cover or barcode.")


@app.post("/api/photo/read")
async def api_photo_read(file: UploadFile = File(...)):
    """Bulk flow: list every book visible in the photo (no lookup)."""
    return {"books": await _read_photo(file)}


@app.post("/api/generate")
async def api_generate(body: GenerateIn):
    try:
        return await ai.enrich(body.book, db.categories(), db.AGE_GROUPS,
                               nudge=body.nudge, previous=body.previous)
    except ai.AIError as e:
        raise HTTPException(502, str(e)) from e


# ---------- inventory ----------

@app.get("/api/books")
def api_books(sort: str = "created_desc", q: str | None = None, ids: str | None = None):
    return db.list_books(sort, q, _ids(ids))


@app.post("/api/books")
def api_create(body: BookIn):
    data = body.model_dump()
    qty = data.pop("quantity")
    if existing := _existing(data):
        # Already in inventory: add the copies instead of creating a duplicate.
        book = db.adjust_quantity(existing["id"], max(qty, 1), "entered again")
        return {**book, "merged": True}
    return db.create_book(data, qty)


@app.get("/api/books/{book_id}")
def api_get(book_id: int):
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found.")
    return {**book, "history": db.stock_history(book_id)}


@app.put("/api/books/{book_id}")
def api_update(book_id: int, body: BookPatch):
    book = db.update_book(book_id, body.model_dump(exclude_unset=True))
    if not book:
        raise HTTPException(404, "Book not found.")
    return book


@app.post("/api/books/{book_id}/quantity")
def api_quantity(book_id: int, body: QuantityIn):
    book = db.adjust_quantity(book_id, body.change, body.note)
    if not book:
        raise HTTPException(404, "Book not found.")
    return book


@app.delete("/api/books/{book_id}")
def api_delete(book_id: int):
    db.delete_book(book_id)
    return {"ok": True}


@app.post("/api/books/delete")
def api_delete_many(body: DeleteIn):
    """Bulk delete; the page asks for confirmation before calling this."""
    return {"deleted": db.delete_books(list(dict.fromkeys(body.ids)))}


@app.post("/api/books/quantities")
def api_quantities(body: QuantitiesIn):
    return db.adjust_many([(c.id, c.change) for c in body.changes], body.note)


# ---------- bulk entry ----------

_create_lock = asyncio.Lock()


def _increment(existing: dict, quantity: int) -> dict:
    book = db.adjust_quantity(existing["id"], quantity, "entered again (bulk)")
    return {"status": "incremented", "book": book, "change": quantity}


@app.post("/api/bulk/parse")
def api_bulk_parse(body: BulkParseIn):
    items = bulk.parse(body.text)
    if not items:
        raise HTTPException(400, "No titles or ISBNs found. Put one book per line, or use a CSV with a Title or ISBN column.")
    return {"items": items}


@app.post("/api/bulk/item")
async def api_bulk_item(body: BulkItemIn):
    """Identify one book and add it: +quantity if already in inventory, else describe and create."""
    query = body.query.strip()
    try:
        if body.work_key or body.isbn13:
            book = await lookup.identify(isbn13=normalize(body.isbn13) if body.isbn13 else None,
                                         work_key=body.work_key, title=query or None, author=body.author)
        elif isbn := normalize(query):
            book = await lookup.identify(isbn13=isbn)
        elif query:
            candidates = await lookup.search(query, body.author)
            match = lookup.best_match(query, body.author, candidates)
            if not match:
                if not candidates:
                    return {"status": "not_found", "message": "No book found with that title."}
                return {"status": "choose", "candidates": candidates[:6]}
            book = await lookup.identify(isbn13=match.get("isbn13"), work_key=match.get("work_key"),
                                         title=match.get("title"), author=match.get("authors"))
            if book and not book.get("cover_url"):
                book["cover_url"] = match.get("cover_url")
        else:
            return {"status": "not_found", "message": "Empty entry."}
    except httpx.HTTPError as e:
        return {"status": "error", "message": f"Book database lookup failed: {e}"}
    if not book:
        return {"status": "not_found", "message": "No matching book found."}

    if existing := _existing(book):
        return _increment(existing, body.quantity)

    try:
        gen = await ai.enrich(book, db.categories(), db.AGE_GROUPS)
    except ai.AIError as e:
        return {"status": "error", "message": str(e), "book": book}
    data = {k: book.get(k) for k in db.BOOK_FIELDS}
    data.update({k: gen[k] for k in ("category", "subgenre", "age_group", "description")})

    async with _create_lock:  # two rows for the same book can finish at the same time
        if existing := _existing(book):
            return _increment(existing, body.quantity)
        created = db.create_book(data, body.quantity)
    return {"status": "added", "book": created, "warnings": gen["warnings"]}


CSV_COLUMNS = [
    ("inventory_code", "Inventory Code"), ("title", "Title"), ("authors", "Author"),
    ("isbn13", "ISBN-13"), ("published", "Publication Date"), ("category", "Category"),
    ("subgenre", "Subgenre"), ("age_group", "Reader Age"), ("description", "Mystery Description"),
    ("quantity", "Quantity"), ("page_count", "Pages"), ("notes", "Notes"),
    ("created_at", "Date Entered"), ("updated_at", "Last Updated"),
]


def _ids(ids: str | None) -> list[int] | None:
    if not ids:
        return None
    try:
        return [int(i) for i in ids.split(",") if i.strip()]
    except ValueError as e:
        raise HTTPException(400, "Bad id list.") from e


@app.get("/api/export.csv")
def api_export(sort: str = "created_desc", q: str | None = None, ids: str | None = None):
    books = db.list_books(sort, q, _ids(ids))
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel reads UTF-8 (e.g. the en dash in "Ages 8–12")
    w = csv.writer(buf)
    w.writerow([label for _, label in CSV_COLUMNS])
    for b in books:
        w.writerow(["" if b.get(k) is None else b[k] for k, _ in CSV_COLUMNS])
    name = f"date-with-a-book-{datetime.now():%Y-%m-%d}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/labels")
def api_labels(ids: str | None = None, sort: str = "created_desc"):
    return {"books": db.list_books(sort, None, _ids(ids)), "settings": db.label_settings()}


# ---------- settings ----------

@app.get("/api/settings")
def api_settings():
    return {"categories": db.categories(), "age_groups": db.AGE_GROUPS,
            "label_settings": db.label_settings()}


@app.put("/api/settings")
def api_put_settings(body: SettingsIn):
    if body.categories is not None:
        cats = list(dict.fromkeys(c.strip() for c in body.categories if c.strip()))
        if not cats:
            raise HTTPException(400, "Keep at least one category.")
        db.set_setting("categories", cats)
    if body.label_settings is not None:
        allowed = set(db.DEFAULT_LABEL_SETTINGS)
        db.set_setting("label_settings", {k: v for k, v in body.label_settings.items() if k in allowed})
    return api_settings()


# ---------- static ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/labels")
def labels_page():
    return FileResponse(STATIC / "labels.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
