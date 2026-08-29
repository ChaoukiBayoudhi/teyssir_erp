from django.db import models

from teyssir.core.models import MONEY, QTY, SyncableModel, TimeStampedModel, UUIDModel


class TaxRate(SyncableModel):
    """TVA rate (spec §15). Tunisia: 7% (books/manuels/journaux/fournitures scolaires),
    13%, 19%, 0%/exonéré."""

    name = models.CharField(max_length=32)
    rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Category(SyncableModel):
    name_fr = models.CharField(max_length=128)
    name_ar = models.CharField(max_length=128, blank=True, default="")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    def __str__(self):
        return self.name_fr


class Product(SyncableModel):
    sku = models.CharField(max_length=48, unique=True)
    internal_code = models.CharField(max_length=48, blank=True, default="")
    name_fr = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True, default="")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    tax_rate = models.ForeignKey(TaxRate, null=True, blank=True, on_delete=models.SET_NULL)

    cost_avg = models.DecimalField(default=0, **MONEY)        # weighted average cost (§14.2)
    sale_price = models.DecimalField(default=0, **MONEY)
    qty_on_hand = models.DecimalField(default=0, **QTY)        # cached fold over the ledger
    reorder_point = models.DecimalField(default=0, **QTY)
    reorder_qty = models.DecimalField(default=0, **QTY)

    is_book = models.BooleanField(default=False)
    isbn = models.CharField(max_length=13, blank=True, default="")
    allow_negative = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.sku} — {self.name_fr}"


class Barcode(SyncableModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="barcodes")
    value = models.CharField(max_length=64, db_index=True)
    symbology = models.CharField(max_length=16, default="EAN13")  # EAN13 / ISBN / CODE128

    class Meta:
        unique_together = [("value", "symbology")]

    def __str__(self):
        return f"{self.symbology}:{self.value}"


class Book(SyncableModel):
    """Rich bibliographic profile for a book product (camera/OCR registration, spec docs/BOOK-OCR).

    `raw_metadata` keeps the full external-provider payload so future enrichment fields need no
    migration; `source_provider`/`ocr_confidence` record how the data was obtained.

    ISBN-13 is optional: Tunisian CNP school editions often use a local ``619…`` product
    barcode only (``edition_kind=school_cnp``). Store that barcode on ``Barcode`` as today.
    """

    SCHOOL_CNP = "school_cnp"
    ISBN_EDITION = "isbn_edition"
    UNKNOWN_EDITION = "unknown"
    EDITION_KINDS = [
        (SCHOOL_CNP, "School / CNP (barcode-only)"),
        (ISBN_EDITION, "ISBN edition (978/979)"),
        (UNKNOWN_EDITION, "Unknown"),
    ]

    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="book")
    isbn13 = models.CharField(max_length=13, blank=True, default="", db_index=True)
    isbn10 = models.CharField(max_length=10, blank=True, default="")
    edition_kind = models.CharField(
        max_length=16, choices=EDITION_KINDS, blank=True, default="", db_index=True,
    )
    subtitle = models.CharField(max_length=255, blank=True, default="")
    publisher = models.CharField(max_length=160, blank=True, default="")
    series = models.CharField(max_length=160, blank=True, default="")
    edition = models.CharField(max_length=80, blank=True, default="")
    languages = models.JSONField(default=list, blank=True)        # e.g. ["ar","fr"]
    pub_year = models.IntegerField(null=True, blank=True)
    pages = models.IntegerField(null=True, blank=True)
    dimensions = models.CharField(max_length=60, blank=True, default="")
    cover_type = models.CharField(max_length=40, blank=True, default="")  # paperback/hardcover
    subject = models.CharField(max_length=160, blank=True, default="")
    keywords = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True, default="")
    source_provider = models.CharField(max_length=40, blank=True, default="")  # openlibrary/ocr/manual
    ocr_confidence = models.FloatField(default=0.0)
    raw_metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Book {self.isbn13 or self.edition_kind or self.product_id}"


class Contributor(SyncableModel):
    """A person credited on books (author/translator/…). Normalized to avoid redundancy."""

    name = models.CharField(max_length=160, unique=True)

    def __str__(self):
        return self.name


class BookContributor(SyncableModel):
    AUTHOR = "AUTHOR"
    TRANSLATOR = "TRANSLATOR"
    EDITOR = "EDITOR"
    ILLUSTRATOR = "ILLUSTRATOR"
    ROLES = [(x, x) for x in (AUTHOR, TRANSLATOR, EDITOR, ILLUSTRATOR)]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="contributors")
    contributor = models.ForeignKey(Contributor, on_delete=models.PROTECT)
    role = models.CharField(max_length=12, choices=ROLES, default=AUTHOR)
    order = models.IntegerField(default=0)

    class Meta:
        unique_together = [("book", "contributor", "role")]
        ordering = ["order"]


class ProductImage(SyncableModel):
    """Original images for a product (book cover/back/pages). Stored via Django ImageField over a
    pluggable storage backend (local FS by default; S3/MinIO via settings, no schema change)."""

    COVER = "COVER"
    BACK = "BACK"
    PAGE = "PAGE"
    OTHER = "OTHER"
    KINDS = [(x, x) for x in (COVER, BACK, PAGE, OTHER)]

    # nullable: a scan stores draft images before the product exists; create_book links them
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="product_images/%Y/%m/")
    kind = models.CharField(max_length=8, choices=KINDS, default=COVER)
    is_primary = models.BooleanField(default=False)
    order = models.IntegerField(default=0)
    ocr_text = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["order", "created_at"]


class ScanJob(UUIDModel, TimeStampedModel):
    """A book-scan request processed by the OCR pipeline. Local-only (never synced to the hub); it
    lets the scan run *asynchronously* so a slow OCR engine (a vision LLM can take tens of seconds)
    doesn't block the HTTP request. The client polls this job until DONE (docs/BOOK-OCR §6).

    Phase 15.5: ``stage`` + ``progress`` (0–100) are additive poll fields so the PWA can show
    pipeline feedback without changing status semantics (still PENDING|DONE|FAILED).
    """

    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"
    STATUSES = [(x, x) for x in (PENDING, DONE, FAILED)]

    # Pipeline milestones (poll UI). Not a DB enum — free text so older clients ignore unknowns.
    STAGE_QUEUED = "queued"
    STAGE_PREPROCESS = "preprocess"
    STAGE_BARCODE = "barcode"
    STAGE_OCR = "ocr"
    STAGE_LANGUAGE = "language"
    STAGE_VISION = "vision"
    STAGE_METADATA = "metadata"
    STAGE_MERGE = "merge"
    STAGE_DONE = "done"
    STAGE_FAILED = "failed"

    status = models.CharField(max_length=8, choices=STATUSES, default=PENDING)
    isbn = models.CharField(max_length=20, blank=True, default="")
    image_ids = models.JSONField(default=list)
    result = models.JSONField(null=True, blank=True)        # the reviewable BookDraft, once DONE
    ocr_text = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    # Phase 15.5 — nullable/blank so older DBs migrate additively without backfill.
    stage = models.CharField(max_length=32, blank=True, null=True, default="queued")
    progress = models.PositiveSmallIntegerField(null=True, blank=True, default=0)

    class Meta:
        ordering = ["-created_at"]
