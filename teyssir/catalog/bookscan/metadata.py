"""Pluggable book-metadata providers (enrich by ISBN). Add providers without schema change —
the full payload is also kept in Book.raw_metadata. Spec docs/BOOK-OCR-ARCHITECTURE.md."""
import json
import re
import urllib.parse
import urllib.request

from .draft import BookDraft


class BookMetadataProvider:
    name = "base"

    def enrich(self, isbn: str):
        raise NotImplementedError


class OpenLibraryProvider(BookMetadataProvider):
    """Free, no API key, decent multilingual coverage (openlibrary.org)."""

    name = "openlibrary"
    URL = "https://openlibrary.org/api/books"

    def _fetch(self, isbn):
        query = urllib.parse.urlencode(
            {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}
        )
        req = urllib.request.Request(f"{self.URL}?{query}", headers={"User-Agent": "Teyssir-ERP"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.load(resp)

    def enrich(self, isbn):
        try:
            rec = self._fetch(isbn).get(f"ISBN:{isbn}")
        except Exception:
            return None
        if not rec:
            return None
        year = re.search(r"\d{4}", rec.get("publish_date", "") or "")
        draft = BookDraft(
            source=self.name, confidence=0.9,
            title=rec.get("title", ""),
            subtitle=rec.get("subtitle", ""),
            authors=[a.get("name", "") for a in rec.get("authors", []) if a.get("name")],
            publisher=(rec.get("publishers") or [{}])[0].get("name", ""),
            pages=rec.get("number_of_pages"),
            pub_year=int(year.group()) if year else None,
            subject=", ".join(s.get("name", "") for s in (rec.get("subjects") or [])[:5]),
            raw=rec,
        )
        if len(isbn) == 13:
            draft.isbn13 = isbn
        elif len(isbn) == 10:
            draft.isbn10 = isbn
        return draft


_PROVIDERS = {
    "openlibrary": OpenLibraryProvider,
    # "googlebooks": GoogleBooksProvider,   # pluggable: free tier, no key for basic lookups
}


def enrich_by_isbn(isbn, providers=None):
    """Try configured providers in order; return the first usable draft (with a title)."""
    from django.conf import settings

    names = providers if providers is not None else settings.METADATA_PROVIDERS
    for name in names:
        cls = _PROVIDERS.get(name)
        if not cls:
            continue
        draft = cls().enrich(isbn)
        if draft and draft.title:
            return draft
    return None
