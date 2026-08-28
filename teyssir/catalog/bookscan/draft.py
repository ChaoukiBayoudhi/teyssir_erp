"""The provider-neutral draft a scan produces, before the user reviews and saves it."""
from dataclasses import asdict, dataclass, field


@dataclass
class BookDraft:
    isbn13: str = ""
    isbn10: str = ""
    # Phase 2B: non-ISBN product identity (CNP 619…, GTIN, Code128, …)
    barcode_raw: str = ""
    barcode_symbology: str = ""
    barcode_kind: str = ""  # isbn13 | gtin | local_product | ""
    title: str = ""
    subtitle: str = ""
    authors: list = field(default_factory=list)
    translators: list = field(default_factory=list)
    publisher: str = ""
    series: str = ""
    edition: str = ""
    languages: list = field(default_factory=list)
    # Phase 15.2: scalar ar|fr|en|mixed:ar+fr (keeps languages[] compatible)
    language_detected: str = ""
    pub_year: int | None = None
    pages: int | None = None
    dimensions: str = ""
    cover_type: str = ""
    subject: str = ""
    keywords: list = field(default_factory=list)
    description: str = ""
    price: str = ""
    currency: str = "TND"
    source: str = ""          # which provider produced it (openlibrary / tesseract / manual)
    confidence: float = 0.0
    raw: dict = field(default_factory=dict)

    _META = {"source", "confidence", "raw"}

    def as_dict(self):
        return asdict(self)

    def merge(self, other):
        """Fill *empty* data fields on self from `other` (self wins). Meta fields are untouched."""
        for key, value in asdict(other).items():
            if key in self._META:
                continue
            if not getattr(self, key) and value:
                setattr(self, key, value)
        return self
