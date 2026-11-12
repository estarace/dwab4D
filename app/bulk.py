"""Parse pasted lists and CSV files into bulk-entry items."""

import csv
import io
import re

from .isbn import normalize

HEADERS = {
    "isbn": {"isbn", "isbn13", "isbn-13", "isbn 13", "isbn10", "isbn-10", "ean", "barcode"},
    "title": {"title", "book", "book title", "name"},
    "author": {"author", "authors", "writer", "by"},
    "quantity": {"quantity", "qty", "copies", "count", "number"},
}
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")
_QTY_SUFFIX = re.compile(r"\s+[x×]\s*(\d{1,3})\s*$", re.I)


def _qty(v) -> int:
    try:
        return max(1, min(999, int(str(v).strip())))
    except (TypeError, ValueError):
        return 1


def _item(query: str, author: str | None = None, quantity: int = 1) -> dict | None:
    query = (query or "").strip().strip('"').strip()
    author = (author or "").strip().strip('"').strip() or None
    if not query:
        return None
    if isbn := normalize(query):
        return {"query": isbn, "author": None, "quantity": quantity}
    return {"query": query, "author": author, "quantity": quantity}


def _from_csv(text: str) -> list[dict] | None:
    """Parse CSV with a recognised header row; None if the header isn't recognised."""
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    cols = {}
    for key, names in HEADERS.items():
        for i, h in enumerate(header):
            if h in names and key not in cols:
                cols[key] = i
    if "isbn" not in cols and "title" not in cols:
        return None

    def cell(row, key):
        i = cols.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    items = []
    for row in rows[1:]:
        isbn, title = cell(row, "isbn"), cell(row, "title")
        quantity = _qty(cell(row, "quantity")) if "quantity" in cols else 1
        if isbn and normalize(isbn):
            item = _item(isbn, None, quantity)
        else:
            item = _item(title or isbn, cell(row, "author"), quantity)
        if item:
            items.append(item)
    return items


def _from_lines(text: str) -> list[dict]:
    items = []
    for line in text.splitlines():
        line = _BULLET.sub("", line).strip()
        if not line or line.startswith("#"):
            continue
        quantity = 1
        if m := _QTY_SUFFIX.search(line):
            quantity, line = _qty(m.group(1)), line[: m.start()]
        if "\t" in line:                                  # pasted from a spreadsheet
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) >= 3 and parts[2].isdigit():
                quantity = _qty(parts[2])
            item = _item(parts[0], parts[1] if len(parts) > 1 else None, quantity)
        elif " by " in line.lower():
            idx = line.lower().rindex(" by ")
            item = _item(line[:idx], line[idx + 4:], quantity)
        else:
            item = _item(line, None, quantity)
        if item:
            items.append(item)
    return items


def parse(text: str) -> list[dict]:
    """Turn pasted text or file contents into [{query, author, quantity}], merging repeats."""
    text = (text or "").lstrip("﻿")
    items = _from_csv(text)
    if items is None:
        items = _from_lines(text)
    merged: dict[tuple, dict] = {}
    for it in items:
        key = (re.sub(r"[^a-z0-9]", "", it["query"].lower()), re.sub(r"[^a-z0-9]", "", (it["author"] or "").lower()))
        if key in merged:
            merged[key]["quantity"] += it["quantity"]
        else:
            merged[key] = dict(it)
    return list(merged.values())
