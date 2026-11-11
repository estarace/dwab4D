"""Book identification via Open Library (primary) and Google Books (optional)."""

import os
import re

import httpx

OL = "https://openlibrary.org"
GB = "https://www.googleapis.com/books/v1/volumes"
HEADERS = {"User-Agent": "DateWithABookHelper/0.1 (personal inventory tool)"}
SEARCH_FIELDS = "key,title,author_name,first_publish_year,cover_i,edition_count,isbn"
DETAIL_FIELDS = "key,title,author_name,first_publish_year,cover_i,number_of_pages_median,subject,person,place"
TIMEOUT = httpx.Timeout(15.0)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)


def _cover(cover_id) -> str | None:
    return f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else None


def _pick_isbn13(isbns: list[str] | None) -> str | None:
    """Pick an English-language edition's ISBN-13 (prefix 978-0 / 978-1), if any."""
    english = sorted(s for s in isbns or [] if len(s) == 13 and s.startswith(("9780", "9781")))
    return english[0] if english else None


async def search(title: str, author: str | None = None, limit: int = 8) -> list[dict]:
    """Title (and optional author) search -> list of candidate works."""
    params = {"title": title, "fields": SEARCH_FIELDS, "limit": limit}
    if author:
        params["author"] = author
    async with _client() as c:
        r = await c.get(f"{OL}/search.json", params=params)
        r.raise_for_status()
        docs = r.json().get("docs", [])
        if not docs and not author:
            # fall back to a general query (handles "title by author" typed together)
            r = await c.get(f"{OL}/search.json", params={"q": title, "fields": SEARCH_FIELDS, "limit": limit})
            r.raise_for_status()
            docs = r.json().get("docs", [])
    return [
        {
            "work_key": d.get("key"),
            "title": d.get("title"),
            "authors": ", ".join(d.get("author_name", [])[:3]),
            "first_published": str(d["first_publish_year"]) if d.get("first_publish_year") else None,
            "cover_url": _cover(d.get("cover_i")),
            "edition_count": d.get("edition_count"),
            "isbn13": _pick_isbn13(d.get("isbn")),
        }
        for d in docs
    ]


async def _google(c: httpx.AsyncClient, isbn13: str | None, title: str | None, author: str | None) -> dict:
    """Best-effort Google Books lookup; returns {} on any failure (it rate-limits without a key)."""
    if isbn13:
        q = f"isbn:{isbn13}"
    elif title:
        q = f"intitle:{title}" + (f"+inauthor:{author}" if author else "")
    else:
        return {}
    params = {"q": q, "maxResults": 1}
    if key := os.environ.get("GOOGLE_BOOKS_API_KEY"):
        params["key"] = key
    try:
        r = await c.get(GB, params=params)
        if r.status_code != 200:
            return {}
        items = r.json().get("items") or []
        if not items:
            return {}
        v = items[0].get("volumeInfo", {})
        isbn = next(
            (i["identifier"] for i in v.get("industryIdentifiers", []) if i.get("type") == "ISBN_13"),
            None,
        )
        return {
            "title": v.get("title"),
            "authors": ", ".join(v.get("authors", [])[:3]),
            "description": v.get("description"),
            "categories": v.get("categories", []),
            "page_count": v.get("pageCount"),
            "published_date": v.get("publishedDate"),
            "maturity": v.get("maturityRating"),
            "isbn13": isbn,
            "cover_url": (v.get("imageLinks") or {}).get("thumbnail"),
        }
    except (httpx.HTTPError, ValueError):
        return {}


def _text(desc) -> str | None:
    if isinstance(desc, dict):
        desc = desc.get("value")
    if not desc:
        return None
    # strip OL markdown links / source notes
    desc = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", desc)
    desc = re.split(r"\n-{3,}|\(\[source\]", desc)[0]
    return desc.strip()


async def identify(isbn13: str | None = None, work_key: str | None = None,
                   title: str | None = None, author: str | None = None) -> dict | None:
    """Gather everything we know about one book. Returns None if nothing matches."""
    async with _client() as c:
        doc = None
        if isbn13:
            r = await c.get(f"{OL}/search.json", params={"isbn": isbn13, "fields": DETAIL_FIELDS, "limit": 1})
            if r.status_code == 200 and r.json().get("docs"):
                doc = r.json()["docs"][0]
        if doc is None and work_key:
            r = await c.get(f"{OL}/search.json", params={"q": f"key:{work_key}", "fields": DETAIL_FIELDS, "limit": 1})
            if r.status_code == 200 and r.json().get("docs"):
                doc = r.json()["docs"][0]
        if doc is None and title:
            params = {"title": title, "fields": DETAIL_FIELDS, "limit": 1}
            if author:
                params["author"] = author
            r = await c.get(f"{OL}/search.json", params=params)
            if r.status_code == 200 and r.json().get("docs"):
                doc = r.json()["docs"][0]

        work = {}
        if doc and doc.get("key"):
            r = await c.get(f"{OL}{doc['key']}.json")
            if r.status_code == 200:
                work = r.json()

        g = await _google(
            c, isbn13,
            title or (doc or {}).get("title"),
            author or ", ".join((doc or {}).get("author_name", [])[:1]) or None,
        )

    if not doc and not g:
        return None
    doc = doc or {}

    first_year = doc.get("first_publish_year")
    first_published = str(first_year) if first_year else None
    if not first_published and g.get("published_date"):
        first_published = g["published_date"][:4]

    ol_desc = _text(work.get("description"))
    summary = ol_desc if ol_desc and len(ol_desc) >= len(g.get("description") or "") else g.get("description")

    return {
        "isbn13": isbn13 or g.get("isbn13"),
        "work_key": doc.get("key"),
        "title": doc.get("title") or g.get("title"),
        "authors": ", ".join(doc.get("author_name", [])[:3]) or g.get("authors"),
        "first_published": first_published,
        "page_count": doc.get("number_of_pages_median") or g.get("page_count"),
        "cover_url": _cover(doc.get("cover_i")) or g.get("cover_url"),
        "source_summary": summary,
        # extra context for the AI and the leak check; not stored
        "subjects": (doc.get("subject") or [])[:25] + g.get("categories", []),
        "characters": [re.sub(r"\s*\(.*?\)\s*", "", p) for p in (doc.get("person") or [])[:20]],
        "places": (doc.get("place") or [])[:10],
        "maturity": g.get("maturity"),
        "edition_published": g.get("published_date"),
    }
