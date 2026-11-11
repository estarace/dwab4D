"""ISBN parsing and validation helpers."""

import re


def clean(raw: str) -> str:
    """Strip everything except digits and a trailing X."""
    return re.sub(r"[^0-9Xx]", "", raw or "").upper()


def is_valid_isbn10(s: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", s):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def is_valid_isbn13(s: str) -> bool:
    if not re.fullmatch(r"\d{13}", s):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s))
    return total % 10 == 0


def to_isbn13(s: str) -> str:
    """Convert a valid ISBN-10 to ISBN-13; ISBN-13 passes through."""
    if len(s) == 13:
        return s
    core = "978" + s[:9]
    check = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core)) % 10) % 10
    return core + str(check)


def normalize(raw: str) -> str | None:
    """Return a valid ISBN-13 for the input, or None if it isn't an ISBN.

    Handles hyphens/spaces, ISBN-10, and barcode scans that include the
    5-digit price add-on (e.g. 978...51299 -> first 13 digits).
    """
    s = clean(raw)
    if len(s) == 10 and is_valid_isbn10(s):
        return to_isbn13(s)
    if len(s) == 13 and is_valid_isbn13(s):
        return s
    if len(s) in (15, 18) and s[:3] in ("978", "979") and is_valid_isbn13(s[:13]):
        return s[:13]
    return None
