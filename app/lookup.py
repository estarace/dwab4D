"""Book identification via Open Library (primary) and Google Books (optional)."""

import os
import re
from datetime import datetime

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
            "original_year": str(d["first_publish_year"]) if d.get("first_publish_year") else None,
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


_DATE_FORMATS = [("%Y-%m-%d", 3), ("%B %d, %Y", 3), ("%b %d, %Y", 3), ("%d %B %Y", 3), ("%d %b %Y", 3),
                 ("%Y-%m", 2), ("%B %Y", 2), ("%b %Y", 2), ("%Y", 1)]


def normalize_date(raw: str | None) -> str | None:
    """'October 1, 1991' -> '1991-10-01', 'Aug 2012' -> '2012-08', '2017' -> '2017'."""
    if not raw:
        return None
    raw = re.sub(r"\s+", " ", str(raw).replace("Sept ", "Sep ")).strip().rstrip(".")
    for fmt, parts in _DATE_FORMATS:
        try:
            d = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return d.strftime(["%Y", "%Y-%m", "%Y-%m-%d"][parts - 1])
    m = re.search(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)", raw)  # e.g. "c1991", "[2004?]"
    return m.group(1) if m else None


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

        # Publication date of this specific edition (what's printed in the copy).
        edition_isbn = isbn13 or g.get("isbn13")
        edition = {}
        if edition_isbn:
            r = await c.get(f"{OL}/isbn/{edition_isbn}.json")
            if r.status_code == 200:
                edition = r.json()

    if not doc and not g:
        return None
    doc = doc or {}

    original_year = str(doc["first_publish_year"]) if doc.get("first_publish_year") else None
    published = (normalize_date(edition.get("publish_date"))
                 or normalize_date(g.get("published_date"))
                 or original_year)

    ol_desc = _text(work.get("description"))
    summary = ol_desc if ol_desc and len(ol_desc) >= len(g.get("description") or "") else g.get("description")

    return {
        "isbn13": isbn13 or g.get("isbn13"),
        "work_key": doc.get("key"),
        "title": doc.get("title") or g.get("title"),
        "authors": ", ".join(doc.get("author_name", [])[:3]) or g.get("authors"),
        "published": published,
        "page_count": doc.get("number_of_pages_median") or g.get("page_count"),
        "cover_url": _cover(doc.get("cover_i")) or g.get("cover_url"),
        "source_summary": summary,
        # extra context for the AI and the leak check; not stored
        "subjects": (doc.get("subject") or [])[:25] + g.get("categories", []),
        "characters": [re.sub(r"\s*\(.*?\)\s*", "", p) for p in (doc.get("person") or [])[:20]],
        "places": (doc.get("place") or [])[:10],
        "maturity": g.get("maturity"),
        "original_year": original_year,  # context for the AI only; not stored
    }


# ---------- confident matching (for bulk entry) ----------

def _norm_title(s: str | None) -> str:
    s = (s or "").lower()
    s = re.split(r"[:(]", s)[0]                      # drop subtitle / series note
    s = re.sub(r"^\s*(the|a|an)\s+", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _author_matches(wanted: str, authors: str | None) -> bool:
    last = _norm((wanted or "").split()[-1]) if wanted and wanted.split() else ""
    return bool(last) and last in _norm(authors)


def best_match(query: str, author: str | None, candidates: list[dict]) -> dict | None:
    """Return the candidate we're confident about, or None if a person should choose.

    Confident = the title matches exactly (ignoring case, punctuation, leading article
    and subtitle) and either the author matches, or the match is clearly the best-known
    book with that title.
    """
    q = _norm_title(query)
    exact = []
    for c in candidates:
        t = _norm_title(c.get("title"))
        if t == q:
            exact.append(c)
        elif not author and t and q.startswith(t):
            # "Gone Girl Gillian Flynn" / "Gone Girl, Gillian Flynn" typed on one line
            rest = q[len(t):]
            if any(rest == _norm(a) for a in (c.get("authors") or "").split(",")):
                return c
    if author:
        exact = [c for c in exact if _author_matches(author, c.get("authors"))]
        return exact[0] if exact else None
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        ranked = sorted(exact, key=lambda c: c.get("edition_count") or 0, reverse=True)
        top, nxt = ranked[0].get("edition_count") or 0, ranked[1].get("edition_count") or 0
        if top >= 5 and top >= 3 * max(nxt, 1):
            return ranked[0]
    return None
