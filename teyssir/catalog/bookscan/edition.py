"""Edition kind: Tunisian CNP school books (barcode-only) vs ISBN novels/romans.

School textbooks from Centre National Pédagogique often carry a local EAN ``619…``
and no ISBN-13. Nouveau livre must treat that as a valid identity path — never
insist on ISBN recrop when school signals are present (even if barcode decode fails).
"""
from __future__ import annotations

import re
from typing import Iterable

EDITION_SCHOOL_CNP = "school_cnp"
EDITION_ISBN = "isbn_edition"
EDITION_UNKNOWN = "unknown"

# Canonical subject repairs for common OCR truncations on CNP covers
_SCHOOL_TITLE_REPAIRS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"math[eé]matiques?", re.I), "Mathématiques"),
    (re.compile(r"^matiques\b|^ématiques\b|^athématiques\b", re.I), "Mathématiques"),
    (re.compile(r"technologie\s+de\s+l['’]?informati", re.I), "Technologie de l'Information"),
    (re.compile(r"^nologie\s+de\s+l['’]?informati", re.I), "Technologie de l'Information"),
    # Arabic History CNP (Case D) — prefer Arabic canonical when script present
    (re.compile(r"التاريخ(?:\s+و(?:ال)?جغرافيا)?"), "التاريخ"),
    (re.compile(r"تاريخ(?:\s+و(?:ال)?جغرافيا)?"), "التاريخ"),
    (re.compile(r"histoire(?:\s+et\s+g[eé]ographie)?", re.I), "Histoire"),
    (re.compile(r"\bsciences?\b", re.I), "Sciences"),
    (re.compile(r"physique(?:\s+chimie)?", re.I), "Physique"),
    (re.compile(r"fran[cç]ais", re.I), "Français"),
    (re.compile(r"anglais", re.I), "Anglais"),
    (re.compile(r"arabe", re.I), "Arabe"),
]

# Strong school-edition signals (title / publisher / cover text)
_SCHOOL_SIGNAL = re.compile(
    r"(?:"
    r"math[eé]matiques?|ématiques|matiques|"
    r"technologie\s+de\s+l['’]?informati|nologie\s+de\s+l['’]?informati|"
    r"centre\s+national(?:\s+p[eé]dagogique)?|\bCNP\b|"
    r"ann[eé]e\s+(?:de\s+l['’]?enseignement|secondaire|primaire)|"
    r"enseignement\s+secondaire|manuel\s+scolaire|"
    r"كتاب|مركز\s+وطني|"
    r"التاريخ|تاريخ|جغرافيا|"
    r"histoire(?:\s+et\s+g[eé]ographie)?|"
    r"2[eè]me\s+ann[eé]e|1[eè]re?\s+ann[eé]e|3[eè]me\s+ann[eé]e|"
    r"tome\s+[12]|السنة\s+(?:الأولى|الثانية|الثالثة)"
    r")",
    re.I,
)

# Subject / grade lines that must never become authors
_SUBJECT_AUTHOR_BLOCK = re.compile(
    r"(?:"
    r"^math|"
    r"ématiques|matiques|mathematiques|"
    r"nologie|technologie|informati|"
    r"histoire|sciences?|physique|chimie|"
    r"fran[cç]ais|anglais|arabe|"
    r"ann[eé]e|tome|secondaire|primaire|"
    r"enseignement|manuel|scolaire|"
    r"^cnp$|centre\s+national|"
    r"كتاب|السنة"
    r")",
    re.I,
)

# Truncated / camelCase OCR blobs that are not person names
_OCR_FRAGMENT_AUTHOR = re.compile(
    r"(?:"
    r"^[a-zà-ÿ]{2,12}$|"  # single lowercase token (ématiques, nologie)
    r"[a-z][A-Z]|"  # mid-word case flip (lInformati)
    r"^l['’]?[A-Z]"
    r")"
)


def _blob(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def looks_like_school_text(*parts: str) -> bool:
    """True when cover/OCR text looks like a Tunisian CNP school textbook."""
    text = _blob(*parts)
    if not text:
        return False
    return bool(_SCHOOL_SIGNAL.search(text))


def is_school_subject_or_fragment(name: str) -> bool:
    """Reject author candidates that are subject titles or OCR truncations."""
    s = (name or "").strip()
    if not s:
        return True
    if _SUBJECT_AUTHOR_BLOCK.search(s):
        return True
    if _OCR_FRAGMENT_AUTHOR.search(s) and len(s) <= 24:
        return True
    # Very short Latin without space — usually a title shard, not an author
    letters = sum(1 for c in s if c.isalpha())
    if letters <= 10 and " " not in s and not re.search(r"[A-ZÀ-Ÿ].*[a-zà-ÿ].*\s", s):
        if re.search(r"(ique|tion|aire|ogie|tique)$", s, re.I):
            return True
    return False


def repair_school_title(title: str, candidates: Iterable[str] | None = None) -> str:
    """Map truncated CNP cover OCR to a clean primary subject title.

    Prefers ``Mathématiques`` when both math and IT lines appear (Math CNP covers).
    """
    pool: list[str] = []
    for raw in [title, *(candidates or [])]:
        s = (raw or "").strip()
        if s and s not in pool:
            pool.append(s)
    if not pool:
        return (title or "").strip()

    repaired: list[str] = []
    seen: set[str] = set()
    for s in pool:
        hit = None
        for pat, canon in _SCHOOL_TITLE_REPAIRS:
            if pat.search(s):
                hit = canon
                break
        if hit and hit not in seen:
            seen.add(hit)
            repaired.append(hit)

    if "Mathématiques" in repaired:
        return "Mathématiques"
    # Prefer Arabic History canonical over French "Histoire" when both match
    if "التاريخ" in repaired:
        return "التاريخ"
    if repaired:
        return repaired[0]

    # Light cleanup: peel leading glue letter on Capitalized subject
    s0 = pool[0]
    m = re.match(r"^([A-Za-z])([A-ZÀ-Ÿ].+)$", s0)
    if m and looks_like_school_text(m.group(2)):
        return m.group(2)
    return s0


def prefer_school_languages(languages: list[str] | None, *, title: str = "") -> list[str]:
    """School CNP covers are French (often bilingual AR+FR) — never tag ``en`` from garbage."""
    langs = [x for x in (languages or []) if x]
    arabic_title = bool(title and re.search(r"[\u0600-\u06FF]", title))
    if looks_like_school_text(title) or any(looks_like_school_text(x) for x in langs):
        out: list[str] = []
        if "ar" in langs or arabic_title:
            out.append("ar")
        # Arabic-primary History covers: keep ar first; add fr only if already tagged
        if arabic_title and "التاريخ" in (title or ""):
            if "fr" in langs and "fr" not in out:
                out.append("fr")
            return out or ["ar"]
        if "fr" not in out:
            out.append("fr")
        # drop en
        return out
    # Strip lone en when French diacritics / school tokens appear in title
    if title and re.search(r"[àâäéèêëïîôùûüç]|Mathématiques|Mathematiques", title, re.I):
        out = [x for x in langs if x != "en"]
        if "fr" not in out:
            out.append("fr")
        return out or ["fr"]
    if arabic_title and re.search(r"التاريخ|تاريخ", title or ""):
        out = [x for x in langs if x != "en"]
        if "ar" not in out:
            out.insert(0, "ar")
        return out or ["ar"]
    return langs


def classify_edition_kind(
    *,
    isbn13: str = "",
    barcode_raw: str = "",
    barcode_kind: str = "",
    title: str = "",
    publisher: str = "",
    description: str = "",
    subject: str = "",
    candidates: Iterable[str] | None = None,
    raw: dict | None = None,
) -> str:
    """Return ``school_cnp`` | ``isbn_edition`` | ``unknown``."""
    raw = raw or {}
    bc = (barcode_raw or "").strip()
    kind = (barcode_kind or "").strip()
    isbn = (isbn13 or "").strip()

    if kind == "local_product" or bc.startswith("619") or raw.get("barcode_non_isbn"):
        return EDITION_SCHOOL_CNP

    blob = _blob(
        title,
        publisher,
        description,
        subject,
        *(candidates or []),
        " ".join(str(x) for x in (raw.get("title_candidates") or [])[:8]),
        str(raw.get("rejected_title") or ""),
        str(raw.get("suggested_title") or ""),
    )
    if looks_like_school_text(blob):
        return EDITION_SCHOOL_CNP

    if isbn.startswith(("978", "979")) or kind == "isbn13":
        return EDITION_ISBN
    if bc.startswith(("978", "979")):
        return EDITION_ISBN

    return EDITION_UNKNOWN


def apply_edition_kind(draft) -> str:
    """Set ``draft.edition_kind`` + ``raw.edition_kind``; return the kind."""
    if draft is None:
        return EDITION_UNKNOWN
    raw = dict(getattr(draft, "raw", None) or {})
    kind = classify_edition_kind(
        isbn13=getattr(draft, "isbn13", "") or "",
        barcode_raw=getattr(draft, "barcode_raw", "") or "",
        barcode_kind=getattr(draft, "barcode_kind", "") or "",
        title=getattr(draft, "title", "") or "",
        publisher=getattr(draft, "publisher", "") or "",
        description=getattr(draft, "description", "") or "",
        subject=getattr(draft, "subject", "") or "",
        candidates=raw.get("title_candidates") or [],
        raw=raw,
    )
    draft.edition_kind = kind
    raw["edition_kind"] = kind
    if kind == EDITION_SCHOOL_CNP:
        raw["school_edition"] = True
        raw["isbn_optional"] = True
    draft.raw = raw
    return kind


def refine_school_draft(draft) -> None:
    """Post-OCR cleanup for CNP school books: title, authors, languages, description."""
    if draft is None:
        return
    kind = apply_edition_kind(draft)
    raw = dict(getattr(draft, "raw", None) or {})
    title = (getattr(draft, "title", "") or "").strip()
    cands = list(raw.get("title_candidates") or [])
    if kind == EDITION_SCHOOL_CNP or looks_like_school_text(title, *cands):
        repaired = repair_school_title(title, cands)
        if repaired and repaired != title:
            if title and not raw.get("rejected_title"):
                raw["pre_repair_title"] = title
            draft.title = repaired
            title = repaired
        # Drop subject-line / fragment authors
        authors = list(getattr(draft, "authors", None) or [])
        kept = [
            a for a in authors
            if a and not is_school_subject_or_fragment(a)
            and a.strip().casefold() not in (title or "").casefold()
        ]
        if len(kept) != len(authors):
            raw["rejected_authors"] = [a for a in authors if a not in kept]
        draft.authors = kept
        # Force fr (or ar+fr), never en-from-garbage
        draft.languages = prefer_school_languages(
            list(getattr(draft, "languages", None) or []), title=title or repaired,
        )
        raw["detected_langs"] = list(draft.languages)
        # Junk template descriptions like "Book: nologie…"
        desc = (getattr(draft, "description", "") or "").strip()
        if desc and (
            re.match(r"^(Book|Livre)\s*[:/]", desc, re.I)
            and (
                "nologie" in desc
                or "ématiques" in desc
                or (title and title[:12].casefold() not in desc.casefold()
                    and repair_school_title(desc) != title)
            )
        ):
            raw["rejected_description"] = desc
            draft.description = ""
            raw["manual_assist"] = True
        kind = apply_edition_kind(draft)
    draft.raw = raw
    draft.edition_kind = kind
