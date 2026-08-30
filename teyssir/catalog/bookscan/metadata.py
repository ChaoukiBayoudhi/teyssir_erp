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


def _norm_title(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def title_similarity(a: str, b: str) -> float:
    """Token Jaccard similarity in [0, 1] for fuzzy title gating."""
    ta = set(_norm_title(a).split())
    tb = set(_norm_title(b).split())
    if not ta or not tb:
        return 0.0
    # Drop ultra-common stopwords that inflate children's-book collisions
    stop = {"the", "a", "an", "le", "la", "les", "de", "des", "du", "et", "and", "of"}
    ta -= stop
    tb -= stop
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _pick_best_ol_doc(docs: list, query_title: str, author: str = ""):
    """Score OpenLibrary search docs; reject weak title-only collisions (e.g. wrong romance)."""
    best = None
    best_score = 0.0
    for doc in docs[:5]:
        cand = doc.get("title") or ""
        sim = title_similarity(query_title, cand)
        # Small bonus when author tokens overlap (OCR author may be wrong — don't require it)
        auth_bonus = 0.0
        if author:
            authors = " ".join(doc.get("author_name") or [])
            auth_bonus = 0.15 * title_similarity(author, authors)
        score = sim + auth_bonus
        if score > best_score:
            best_score = score
            best = (doc, sim, score)
    if not best or best[1] < 0.45:
        return None
    return best


def _ol_search(title: str, author: str = ""):
    params = {"title": title, "limit": 5}
    if author:
        params["author"] = author
    data = _http_json("https://openlibrary.org/search.json?" + urllib.parse.urlencode(params))
    docs = data.get("docs") or []
    if not docs:
        return None
    picked = _pick_best_ol_doc(docs, title, author)
    if not picked:
        # Retry without author filter — OCR author is often a series/imprint line
        if author:
            params.pop("author", None)
            data = _http_json(
                "https://openlibrary.org/search.json?" + urllib.parse.urlencode(params)
            )
            docs = data.get("docs") or []
            picked = _pick_best_ol_doc(docs, title, "")
        if not picked:
            return None
    doc, sim, score = picked
    year = doc.get("first_publish_year")
    isbns = doc.get("isbn") or []
    isbn13 = next((to_isbn13(x) for x in isbns if to_isbn13(x)), "")
    # Author overlap required for a "strong" hit — same title alone is too collision-prone
    # (e.g. many unrelated "Beauty and the Beast" editions).
    auth_sim = 0.0
    if author:
        auth_sim = title_similarity(author, " ".join(doc.get("author_name") or []))
    strong = sim >= 0.75 and auth_sim >= 0.4
    # Title-only search is assistive; never claim ISBN-level confidence without a scanned ISBN.
    conf = 0.45 if strong else 0.35
    use_isbn = isbn13 if strong else ""
    ol_authors = list(doc.get("author_name") or [])
    # Only trust OL authors when the query author matched; otherwise leave empty for OCR/user.
    authors = ol_authors if strong else (ol_authors if not author else [])
    return BookDraft(
        source="openlibrary", confidence=conf,
        title=doc.get("title") or title,
        authors=authors,
        publisher=(doc.get("publisher") or [""])[0] if doc.get("publisher") else "",
        pub_year=int(year) if year else None,
        isbn13=use_isbn,
        languages=list(doc.get("language") or [])[:3],
        raw={
            "search": doc, "query_title": title, "title_similarity": round(sim, 3),
            "author_similarity": round(auth_sim, 3),
            "title_search_weak": not strong,
        },
    )


def _gb_search(title: str, author: str = ""):
    q = f'intitle:"{title}"'
    if author:
        q += f' inauthor:"{author}"'
    data = _http_json(
        "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode({"q": q, "maxResults": 5})
    )
    items = data.get("items") or []
    if not items:
        return None
    best = None
    best_sim = 0.0
    for item in items:
        info = item.get("volumeInfo") or {}
        cand = info.get("title") or ""
        if not cand:
            continue
        sim = title_similarity(title, cand)
        if sim > best_sim:
            best_sim = sim
            best = info
    if not best or best_sim < 0.45:
        return None
    info = best
    ids = {i.get("type"): i.get("identifier") for i in (info.get("industryIdentifiers") or [])}
    year = None
    m = re.search(r"\d{4}", info.get("publishedDate") or "")
    if m:
        year = int(m.group())
    strong = best_sim >= 0.75
    if author:
        auth_blob = " ".join(info.get("authors") or [])
        strong = strong and title_similarity(author, auth_blob) >= 0.4
    isbn13 = to_isbn13(ids.get("ISBN_13") or "") or ""
    authors = list(info.get("authors") or [])
    if not strong:
        authors = authors if not author else []
    return BookDraft(
        source="googlebooks", confidence=0.45 if strong else 0.35,
        title=info.get("title") or title,
        subtitle=info.get("subtitle") or "",
        authors=authors or ([author] if author and strong else []),
        publisher=info.get("publisher") or "",
        pub_year=year,
        pages=info.get("pageCount"),
        languages=[info["language"]] if info.get("language") else [],
        isbn13=isbn13 if strong else "",
        isbn10=(ids.get("ISBN_10") or "") if strong else "",
        description=(info.get("description") or "")[:2000],
        raw={
            "search": info, "query_title": title, "title_similarity": round(best_sim, 3),
            "title_search_weak": not strong,
        },
    )


def enrich_by_title(title: str, author: str = "", providers=None):
    """Fuzzy/metadata search when no ISBN is available (Tunisian local editions, school books).

    Rejects weak collisions (generic titles matching unrelated authors) and caps confidence
    so the UI does not look like a high-trust ISBN hit.
    """
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
