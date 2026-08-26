"""Pluggable book-metadata providers (enrich by ISBN). Add providers without schema change —
the full payload is also kept in Book.raw_metadata. Spec docs/BOOK-OCR-ARCHITECTURE.md.

Order (settings.METADATA_PROVIDERS): Open Library first (free), then Google Books fallback.
"""
import json
import re
import urllib.parse
import urllib.request

from .draft import BookDraft
from .isbn import to_isbn13


class BookMetadataProvider:
    name = "base"

    def enrich(self, isbn: str):
        raise NotImplementedError


def _http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Teyssir-ERP/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class OpenLibraryProvider(BookMetadataProvider):
    """Free, no API key, decent multilingual coverage (openlibrary.org)."""

    name = "openlibrary"
    URL = "https://openlibrary.org/api/books"

    def enrich(self, isbn):
        isbn = to_isbn13(isbn) or (isbn or "").strip()
        if not isbn:
            return None
        try:
            query = urllib.parse.urlencode(
                {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}
            )
            rec = _http_json(f"{self.URL}?{query}").get(f"ISBN:{isbn}")
        except Exception:
            return None
        if not rec:
            return None
        year = re.search(r"\d{4}", rec.get("publish_date", "") or "")
        draft = BookDraft(
            source=self.name, confidence=0.9,
            title=rec.get("title", ""),
            subtitle=rec.get("subtitle", "") or "",
            authors=[a.get("name", "") for a in rec.get("authors", []) if a.get("name")],
            publisher=(rec.get("publishers") or [{}])[0].get("name", ""),
            pages=rec.get("number_of_pages"),
            pub_year=int(year.group()) if year else None,
            subject=", ".join(s.get("name", "") for s in (rec.get("subjects") or [])[:5]),
            isbn13=isbn if len(isbn) == 13 else "",
            isbn10=isbn if len(isbn) == 10 else "",
            raw=rec,
        )
        return draft


class GoogleBooksProvider(BookMetadataProvider):
    """Free Google Books volumes API (no key required for light traffic)."""

    name = "googlebooks"
    URL = "https://www.googleapis.com/books/v1/volumes"

    def enrich(self, isbn):
        isbn = to_isbn13(isbn) or (isbn or "").strip()
        if not isbn:
            return None
        try:
            query = urllib.parse.urlencode({"q": f"isbn:{isbn}"})
            data = _http_json(f"{self.URL}?{query}")
        except Exception:
            return None
        items = data.get("items") or []
        if not items:
            return None
        info = items[0].get("volumeInfo") or {}
        title = info.get("title") or ""
        if not title:
            return None
        year = None
        published = info.get("publishedDate") or ""
        m = re.search(r"\d{4}", published)
        if m:
            year = int(m.group())
        ids = {i.get("type"): i.get("identifier") for i in (info.get("industryIdentifiers") or [])}
        draft = BookDraft(
            source=self.name, confidence=0.85,
            title=title,
            subtitle=info.get("subtitle") or "",
            authors=list(info.get("authors") or []),
            publisher=info.get("publisher") or "",
            pages=info.get("pageCount"),
            pub_year=year,
            languages=[info["language"]] if info.get("language") else [],
            subject=", ".join((info.get("categories") or [])[:5]),
            description=(info.get("description") or "")[:2000],
            isbn13=to_isbn13(ids.get("ISBN_13") or isbn) or isbn,
            isbn10=ids.get("ISBN_10") or "",
            raw=info,
        )
        return draft


_PROVIDERS = {
    "openlibrary": OpenLibraryProvider,
    "googlebooks": GoogleBooksProvider,
}


def enrich_by_isbn(isbn, providers=None):
    """Try configured providers in order; return the first usable draft (with a title)."""
    from django.conf import settings

    isbn = to_isbn13(isbn) or (isbn or "").strip()
    if not isbn:
        return None
    names = providers if providers is not None else settings.METADATA_PROVIDERS
    for name in names:
        cls = _PROVIDERS.get(name)
        if not cls:
            continue
        draft = cls().enrich(isbn)
        if draft and draft.title:
            draft.isbn13 = draft.isbn13 or isbn
            draft.raw = {**(draft.raw or {}), "isbn_detected": True, "metadata_provider": name}
            return draft
    return None


def _ol_search(title: str, author: str = ""):
    params = {"title": title, "limit": 3}
    if author:
        params["author"] = author
    data = _http_json("https://openlibrary.org/search.json?" + urllib.parse.urlencode(params))
    docs = data.get("docs") or []
    if not docs:
        return None
    doc = docs[0]
    year = doc.get("first_publish_year")
    isbns = doc.get("isbn") or []
    isbn13 = next((to_isbn13(x) for x in isbns if to_isbn13(x)), "")
    return BookDraft(
        source="openlibrary", confidence=0.7,
        title=doc.get("title") or title,
        authors=list(doc.get("author_name") or ([author] if author else [])),
        publisher=(doc.get("publisher") or [""])[0] if doc.get("publisher") else "",
        pub_year=int(year) if year else None,
        isbn13=isbn13 or "",
        languages=list(doc.get("language") or [])[:3],
        raw={"search": doc, "query_title": title},
    )


def _gb_search(title: str, author: str = ""):
    q = f'intitle:"{title}"'
    if author:
        q += f' inauthor:"{author}"'
    data = _http_json(
        "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode({"q": q, "maxResults": 3})
    )
    items = data.get("items") or []
    if not items:
        return None
    info = items[0].get("volumeInfo") or {}
    if not info.get("title"):
        return None
    ids = {i.get("type"): i.get("identifier") for i in (info.get("industryIdentifiers") or [])}
    year = None
    m = re.search(r"\d{4}", info.get("publishedDate") or "")
    if m:
        year = int(m.group())
    return BookDraft(
        source="googlebooks", confidence=0.65,
        title=info.get("title") or title,
        subtitle=info.get("subtitle") or "",
        authors=list(info.get("authors") or ([author] if author else [])),
        publisher=info.get("publisher") or "",
        pub_year=year,
        pages=info.get("pageCount"),
        languages=[info["language"]] if info.get("language") else [],
        isbn13=to_isbn13(ids.get("ISBN_13") or "") or "",
        isbn10=ids.get("ISBN_10") or "",
        description=(info.get("description") or "")[:2000],
        raw={"search": info, "query_title": title},
    )


def enrich_by_title(title: str, author: str = "", providers=None):
    """Fuzzy/metadata search when no ISBN is available (Tunisian local editions, school books)."""
    from django.conf import settings

    title = (title or "").strip()
    if len(title) < 3:
        return None
    author = (author or "").strip()
    names = providers if providers is not None else settings.METADATA_PROVIDERS
    for name in names:
        try:
            if name == "openlibrary":
                draft = _ol_search(title, author)
            elif name == "googlebooks":
                draft = _gb_search(title, author)
            else:
                continue
        except Exception:
            draft = None
        if draft and draft.title:
            draft.raw = {**(draft.raw or {}), "title_search": True, "metadata_provider": name}
            return draft
    return None
