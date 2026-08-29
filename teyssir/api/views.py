from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from teyssir.catalog.models import Barcode, Product, TaxRate
from teyssir.core.money import display
from teyssir.core.qty import format_qty
from teyssir.customers.models import Customer
from teyssir.customers.services import balance, charge_account, post_payment, statement
from teyssir.inventory.services import post_stocktake
from teyssir.reports.services import consolidated_sales_by_store, sales_report
from teyssir.purchasing.models import PurchaseOrder, Supplier
from teyssir.purchasing.services import create_po, receive_direct, receive_po, record_purchase_invoice
from teyssir.quotations.models import Quotation, Reservation
from teyssir.quotations.services import (
    convert_quotation, create_quotation, create_reservation, release_reservation,
)
from teyssir.sales.cash import current_session, open_session, x_report, z_report
from teyssir.sales.models import Sale, SaleLine
from teyssir.sales.services import finalize_sale, process_return

from .serializers import (
    CheckoutSerializer, CustomerSerializer, POCreateSerializer, ProductSerializer,
    PurchaseInvoiceCreateSerializer, PurchaseOrderSerializer, QuotationCreateSerializer,
    ReceiveSerializer, ReservationCreateSerializer, ReturnSerializer, StockTakeSerializer,
    SupplierSerializer, TaxRateSerializer,
)


def capability(codename):
    """Build a DRF permission gating on an RBAC capability (spec §10)."""

    class _Capability(BasePermission):
        message = f"Requires the '{codename}' permission."

        def has_permission(self, request, view):
            user = request.user
            return bool(
                user
                and user.is_authenticated
                and (user.is_superuser or user.has_perm(f"accounts.{codename}"))
            )

    return _Capability


CanViewReports = capability("view_financial_reports")


class ProductViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Catalog read + search + barcode lookup for the POS (spec §12/§13).

    Query params:
      - ``search`` / ``q``: unified ranked search (name FR/AR, SKU, reference, ISBN, barcode)
      - ``barcode``: exact barcode / SKU / reference / ISBN (scanner path)
    """

    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from teyssir.catalog.search import lookup_by_code, search_products

        qs = Product.objects.filter(active=True).select_related("tax_rate")
        barcode = self.request.query_params.get("barcode")
        search = (
            self.request.query_params.get("search")
            or self.request.query_params.get("q")
            or ""
        )
        if barcode:
            return lookup_by_code(barcode, base=qs).order_by("name_fr")[:50]
        if search.strip():
            return search_products(search, base=qs)
        return qs.order_by("name_fr")[:50]


def _catalog_row(request, p, primary):
    """Compact row for the catalogue browser (list view)."""
    img = primary.get(p.id)
    reorder = p.reorder_point or 0
    return {
        "id": str(p.id), "sku": p.sku, "reference": p.reference, "name_fr": p.name_fr,
        "name_ar": p.name_ar, "sale_price": str(p.sale_price),
        "qty_on_hand": format_qty(p.qty_on_hand),
        "reorder_point": format_qty(reorder), "is_book": p.is_book,
        "product_type": p.product_type, "color": p.color, "brand": p.brand,
        "category": p.category.name_fr if p.category_id else "",
        "out_of_stock": p.qty_on_hand <= 0,
        "low_stock": bool(reorder) and 0 < p.qty_on_hand <= reorder,
        "image": request.build_absolute_uri(img.image.url) if img else None,
    }


class CatalogSearchView(APIView):
    """GET /catalog/search — paginated, multi-criteria catalogue browser (search + filter + sort).

    Params: q (name/sku/isbn/internal-code/barcode/publisher/subtitle/author, partial, case-insensitive),
    category, type=book|supply, stock=in|low|out, ordering=name|-name|price|-price|stock|-stock,
    page, page_size. Returns {count, page, page_size, num_pages, results}."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import math

        from django.db.models import F

        from teyssir.catalog.models import Product, ProductImage
        from teyssir.catalog.search import catalog_text_filter

        qs = Product.objects.filter(active=True).select_related("category")

        q = (request.query_params.get("q") or request.query_params.get("search") or "").strip()
        if q:
            qs = catalog_text_filter(qs, q)

        if request.query_params.get("category"):
            qs = qs.filter(category_id=request.query_params["category"])
        typ = request.query_params.get("type")
        if typ == "book":
            qs = qs.filter(Q(is_book=True) | Q(product_type=Product.BOOK))
        elif typ in ("supply", "furniture"):
            qs = qs.filter(is_book=False, product_type=Product.FURNITURE)
        stock = request.query_params.get("stock")
        if stock == "in":
            qs = qs.filter(qty_on_hand__gt=0)
        elif stock == "out":
            qs = qs.filter(qty_on_hand__lte=0)
        elif stock == "low":
            qs = qs.filter(qty_on_hand__gt=0, qty_on_hand__lte=F("reorder_point"),
                           reorder_point__gt=0)

        order_map = {"name": "name_fr", "-name": "-name_fr", "price": "sale_price",
                     "-price": "-sale_price", "stock": "qty_on_hand", "-stock": "-qty_on_hand"}
        qs = qs.order_by(order_map.get(request.query_params.get("ordering"), "name_fr"))

        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(100, max(1, int(request.query_params.get("page_size", 25))))
        except (TypeError, ValueError):
            page_size = 25

        count = qs.count()
        rows = list(qs[(page - 1) * page_size: (page - 1) * page_size + page_size])
        images = (ProductImage.objects.filter(product_id__in=[r.id for r in rows])
                  .order_by("product_id", "-is_primary", "order"))
        primary = {}
        for img in images:
            primary.setdefault(img.product_id, img)     # primary (—is_primary first) or first image
        return Response({
            "count": count, "page": page, "page_size": page_size,
            "num_pages": max(1, math.ceil(count / page_size)),
            "results": [_catalog_row(request, p, primary) for p in rows],
        })


class BarcodeLookupView(APIView):
    """GET /catalog/lookup?barcode=XXXX — resolve a scanned barcode (hardware reader or camera) to
    an existing product, or report that it is unknown so the UI can offer to register it."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from teyssir.catalog.search import lookup_by_code

        code = (request.query_params.get("barcode") or request.query_params.get("q") or "").strip()
        if not code:
            return Response({"found": False, "barcode": code})
        p = lookup_by_code(code).select_related("tax_rate").first()
        if not p:
            return Response({"found": False, "barcode": code})
        return Response({"found": True, "barcode": code, "product": {
            "id": str(p.id), "sku": p.sku, "reference": p.reference,
            "name_fr": p.name_fr, "name_ar": p.name_ar,
            "sale_price": str(p.sale_price), "qty_on_hand": format_qty(p.qty_on_hand),
            "is_book": p.is_book, "product_type": p.product_type, "active": p.active,
        }})


class ProductCreateView(APIView):
    """POST /catalog/register — register ANY article (book or supply) with its barcode + opening
    stock. Books normally use the richer scan flow, but supplies (fournitures) register here."""

    permission_classes = [capability("edit_product")]

    def post(self, request):
        from teyssir.catalog.services import create_product

        d = request.data
        if not (d.get("name_fr") or "").strip():
            return Response({"detail": "name_fr is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            p = create_product(
                name_fr=d["name_fr"], name_ar=d.get("name_ar", ""),
                category_id=d.get("category") or None, tax_rate_id=d.get("tax_rate") or None,
                sale_price=d.get("sale_price", "0"),
                is_book=bool(d.get("is_book")),
                product_type=d.get("product_type", ""),
                reference=d.get("reference", "") or d.get("sku", ""),
                color=d.get("color", ""), brand=d.get("brand", ""),
                barcode=d.get("barcode", ""), symbology=d.get("symbology", ""),
                initial_qty=d.get("initial_qty", "0"), cost=d.get("cost", "0"),
                reorder_point=d.get("reorder_point", "0"),
                isbn=d.get("isbn", ""),
                origin_terminal=settings.TERMINAL,
            )
        except ValueError as exc:
            msg = str(exc)
            code = status.HTTP_409_CONFLICT if "déjà" in msg or "existe déjà" in msg else status.HTTP_400_BAD_REQUEST
            return Response({"detail": msg}, status=code)
        return Response({"id": str(p.id), "sku": p.sku, "reference": p.reference,
                         "name": p.name_fr, "product_type": p.product_type},
                        status=status.HTTP_201_CREATED)


class PdfToDocxView(APIView):
    """POST /tools/pdf-to-docx — convert a PDF to Word (.docx).

    * Tiny text PDFs (≤2 MB, ≤5 pages): run inline and return **200** + FileResponse
      (backward-compatible with the original sync client / tests).
    * Larger / slow jobs: create a ``ConvertJob``, enqueue (inline|thread), return
      **202** ``{job_id, status}`` — client polls ``GET …/<job_id>`` then downloads.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        import os
        import time

        from django.conf import settings
        from django.http import HttpResponse

        from teyssir.core.convert_jobs import enqueue_convert
        from teyssir.core.models import ConvertJob
        from teyssir.core.pdfconvert import (
            convert_pdf_to_docx, convert_workspace, profile_pdf, validate_pdf_header,
        )

        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        mode = (request.data.get("mode") or ConvertJob.AUTO).lower()
        if mode not in (ConvertJob.FAST, ConvertJob.LAYOUT, ConvertJob.AUTO):
            mode = ConvertJob.AUTO
        force_async = str(request.data.get("async", "")).lower() in ("1", "true", "yes")

        # Stream to a job workspace via chunks — never upload.read() into a giant buffer first.
        job = ConvertJob.objects.create(
            status=ConvertJob.PENDING, mode=mode,
            original_name=os.path.basename(upload.name or "document.pdf"),
        )
        workspace = convert_workspace(job.id)
        src_abs = os.path.join(workspace, "in.pdf")
        size = 0
        try:
            with open(src_abs, "wb") as out:
                for chunk in upload.chunks():
                    size += len(chunk)
                    if size > 25 * 1024 * 1024:
                        raise ValueError("PDF larger than 25 MB")
                    out.write(chunk)
            with open(src_abs, "rb") as fh:
                pdf_bytes = fh.read()
            validate_pdf_header(pdf_bytes)
            profile = profile_pdf(pdf_bytes)
        except ValueError as exc:
            job.status = ConvertJob.FAILED
            job.error = str(exc)
            job.save(update_fields=["status", "error", "updated_at"])
            self._cleanup_job_files(job)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            job.status = ConvertJob.FAILED
            job.error = "invalid upload"
            job.save(update_fields=["status", "error", "updated_at"])
            self._cleanup_job_files(job)
            return Response({"detail": "conversion failed — the PDF may be damaged or protected"},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        job.input_path = os.path.join("convert", str(job.id), "in.pdf")
        job.output_path = os.path.join("convert", str(job.id), "out.docx")
        job.page_count = profile.pages
        job.save(update_fields=["input_path", "output_path", "page_count", "updated_at"])

        # Sync fast-path for tiny PDFs (unless client forced async).
        if profile.fits_sync and not force_async:
            t0 = time.perf_counter()
            try:
                docx_bytes, used, _ = convert_pdf_to_docx(pdf_bytes, mode=mode)
            except Exception:
                job.status = ConvertJob.FAILED
                job.error = "conversion failed"
                job.save(update_fields=["status", "error", "updated_at"])
                return Response({"detail": "conversion failed — the PDF may be damaged or protected"},
                                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            dst_abs = os.path.join(str(settings.MEDIA_ROOT), job.output_path)
            with open(dst_abs, "wb") as fh:
                fh.write(docx_bytes)
            job.status = ConvertJob.DONE
            job.mode_used = used
            job.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            from django.utils import timezone
            job.finished_at = timezone.now()
            job.save()
            name = os.path.splitext(job.original_name or "document")[0] + ".docx"
            resp = HttpResponse(
                docx_bytes,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            resp["Content-Disposition"] = f'attachment; filename="{name}"'
            resp["X-Convert-Job-Id"] = str(job.id)
            resp["X-Convert-Mode"] = used
            return resp

        enqueue_convert(job.id)
        return Response(
            {
                "job_id": str(job.id),
                "status": "pending",
                "pages": profile.pages,
                "mode": mode,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @staticmethod
    def _cleanup_job_files(job):
        import os
        import shutil

        from django.conf import settings

        try:
            root = os.path.join(str(settings.MEDIA_ROOT), "convert", str(job.id))
            shutil.rmtree(root, ignore_errors=True)
        except Exception:
            pass


class PdfToDocxJobView(APIView):
    """GET /tools/pdf-to-docx/<job_id> — poll conversion status."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from teyssir.core.models import ConvertJob

        job = ConvertJob.objects.filter(pk=pk).first()
        if not job:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        payload = {
            "job_id": str(job.id),
            "status": job.status.lower(),
            "mode": job.mode,
            "mode_used": job.mode_used,
            "pages": job.page_count,
            "elapsed_ms": job.elapsed_ms,
            "original_name": job.original_name,
        }
        if job.status == ConvertJob.DONE:
            payload["download_url"] = f"/api/v1/tools/pdf-to-docx/{job.id}/download"
        if job.status == ConvertJob.FAILED:
            payload["error"] = job.error or "conversion failed"
        return Response(payload)


class PdfToDocxDownloadView(APIView):
    """GET /tools/pdf-to-docx/<job_id>/download — stream the .docx via FileResponse."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        import os

        from django.conf import settings
        from django.http import FileResponse

        from teyssir.core.models import ConvertJob

        job = ConvertJob.objects.filter(pk=pk).first()
        if not job:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if job.status != ConvertJob.DONE:
            return Response({"detail": f"job is {job.status.lower()}"},
                            status=status.HTTP_409_CONFLICT)
        abs_path = os.path.join(str(settings.MEDIA_ROOT), job.output_path)
        if not os.path.isfile(abs_path):
            return Response({"detail": "output missing"}, status=status.HTTP_404_NOT_FOUND)
        name = os.path.splitext(job.original_name or "document")[0] + ".docx"
        resp = FileResponse(
            open(abs_path, "rb"),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        resp["Content-Disposition"] = f'attachment; filename="{name}"'
        resp["X-Convert-Mode"] = job.mode_used or job.mode
        return resp


class CategoryListView(APIView):
    """GET /catalog/categories — categories for the catalogue filter dropdown."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from teyssir.catalog.models import Category

        return Response([{"id": str(c.id), "name": c.name_fr}
                         for c in Category.objects.order_by("name_fr")])


class ProductDetailView(APIView):
    """GET /catalog/products/<pk>/detail — full product profile.
    PATCH — update editable catalogue fields (``edit_product``).
    Optional ``qty_on_hand`` sets absolute stock via a STOCKTAKE ledger adjustment
    (same path as inventory stocktake — never write the cache alone).
    DELETE — soft-delete (``active=False``) so the row leaves catalogue/POS search."""

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [capability("edit_product")()]
        return [IsAuthenticated()]

    def _get_product(self, pk, *, active_only=False):
        from teyssir.catalog.models import Product

        qs = Product.objects.select_related("category", "tax_rate")
        if active_only:
            qs = qs.filter(active=True)
        return qs.filter(pk=pk).first()

    def _detail_payload(self, request, p):
        from teyssir.catalog.models import ProductImage

        data = {
            "id": str(p.id), "sku": p.sku, "reference": p.reference,
            "internal_code": p.internal_code,
            "name_fr": p.name_fr, "name_ar": p.name_ar,
            "category": p.category.name_fr if p.category_id else "",
            "category_id": str(p.category_id) if p.category_id else "",
            "tax_rate": str(p.tax_rate_id) if p.tax_rate_id else "",
            "is_book": p.is_book, "product_type": p.product_type,
            "isbn": p.isbn, "color": p.color, "brand": p.brand, "active": p.active,
            "sale_price": str(p.sale_price), "cost_avg": str(p.cost_avg),
            "qty_on_hand": format_qty(p.qty_on_hand), "reorder_point": format_qty(p.reorder_point),
            "reorder_qty": format_qty(p.reorder_qty),
            "tax_rate_percent": str(p.tax_rate.rate_percent) if p.tax_rate_id else "0",
            "barcodes": list(p.barcodes.values("value", "symbology")),
            "images": [_image_payload(request, i)
                       for i in ProductImage.objects.filter(product=p).order_by("-is_primary", "order")],
        }
        book = getattr(p, "book", None)
        if book:
            data["book"] = {
                "isbn13": book.isbn13, "isbn10": book.isbn10, "subtitle": book.subtitle,
                "publisher": book.publisher, "series": book.series, "edition": book.edition,
                "edition_kind": book.edition_kind,
                "languages": book.languages, "pub_year": book.pub_year, "pages": book.pages,
                "dimensions": book.dimensions, "cover_type": book.cover_type,
                "subject": book.subject, "description": book.description,
                "contributors": [
                    {"name": bc.contributor.name, "role": bc.role}
                    for bc in book.contributors.select_related("contributor").order_by("order")
                ],
            }
        return data

    def get(self, request, pk):
        p = self._get_product(pk)
        if not p:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self._detail_payload(request, p))

    def patch(self, request, pk):
        from teyssir.catalog.services import update_product

        p = self._get_product(pk, active_only=True)
        if not p:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        d = request.data
        kwargs = {}
        for key in ("name_fr", "name_ar", "sale_price", "reorder_point", "reference",
                    "color", "brand", "isbn"):
            if key in d:
                kwargs[key] = d.get(key)
        if "category" in d:
            cat = d.get("category")
            if cat in (None, ""):
                kwargs["clear_category"] = True
            else:
                kwargs["category_id"] = cat
        if "tax_rate" in d:
            tax = d.get("tax_rate")
            if tax in (None, ""):
                kwargs["clear_tax_rate"] = True
            else:
                kwargs["tax_rate_id"] = tax
        try:
            p = update_product(p, **kwargs)
        except ValueError as exc:
            msg = str(exc)
            code = status.HTTP_409_CONFLICT if "déjà" in msg or "existe déjà" in msg else status.HTTP_400_BAD_REQUEST
            return Response({"detail": msg}, status=code)

        # Absolute stock set → STOCKTAKE variance on the ledger (spec §14.4), not a direct cache write.
        if "qty_on_hand" in d:
            from teyssir.core.qty import QtyError, to_qty

            try:
                counted = to_qty(d.get("qty_on_hand"), allow_negative=False, label="qty_on_hand")
            except QtyError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            if counted != int(p.qty_on_hand or 0):
                post_stocktake(
                    [{"product_id": p.id, "counted_qty": counted}],
                    terminal=settings.TERMINAL,
                )
                p.refresh_from_db()

        return Response(self._detail_payload(request, p))

    def delete(self, request, pk):
        from teyssir.catalog.services import deactivate_product

        p = self._get_product(pk, active_only=True)
        if not p:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        deactivate_product(p)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaxRateViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = TaxRate.objects.all()
    serializer_class = TaxRateSerializer
    permission_classes = [IsAuthenticated]


class CustomerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Customers + their credit-account statement and on-account payments (spec §M9)."""

    queryset = Customer.objects.filter(active=True).order_by("name")
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        from django.conf import settings
        from teyssir.sync.services import enqueue_customer
        customer = serializer.save(origin_terminal=settings.TERMINAL)
        enqueue_customer(customer)   # quick-created at a till -> sync up to the hub (§4.4)

    @action(detail=True, methods=["get"])
    def statement(self, request, pk=None):
        return Response(statement(self.get_object()))

    @action(detail=True, methods=["post"])
    def payment(self, request, pk=None):
        from teyssir.customers.services import AccountAmountError

        customer = self.get_object()
        amount = request.data.get("amount")
        if amount in (None, ""):
            return Response({"detail": "amount required"}, status=status.HTTP_400_BAD_REQUEST)
        allow_overpay = str(request.data.get("allow_overpay", "")).lower() in ("1", "true", "yes")
        try:
            post_payment(
                customer, amount, note=request.data.get("note", ""),
                allow_overpay=allow_overpay,
            )
        except AccountAmountError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"balance": str(balance(customer))}, status=status.HTTP_201_CREATED)


class CheckoutView(APIView):
    """Build a sale from a cart and finalize it (offline-capable, spec §13)."""

    permission_classes = [capability("create_sale")]

    @transaction.atomic
    def post(self, request):
        from teyssir.sales.services import DiscountError

        ser = CheckoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        customer_id = data.get("customer")
        sale = Sale.objects.create(
            terminal=data["terminal"], status=Sale.DRAFT,
            customer_id=str(customer_id) if customer_id else "",
            discount=data.get("discount") or 0,
            cash_session=current_session(data["terminal"]),  # attribute to the open shift (§13.3)
            created_by=request.user, origin_terminal=data["terminal"],
        )
        for ln in data["lines"]:
            product = Product.objects.get(pk=ln["product"])
            SaleLine.objects.create(
                sale=sale, product=product,
                qty=ln["qty"],
                unit_price=ln.get("unit_price") or product.sale_price,
                discount=ln.get("discount") or 0,
                tax_rate=(product.tax_rate.rate_percent if product.tax_rate else 0),
                origin_terminal=data["terminal"],
            )
        try:
            invoice = finalize_sale(sale, payment_method=data["payment_method"])
        except DiscountError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if data["payment_method"] == "ACCOUNT" and customer_id:
            charge_account(Customer.objects.get(pk=customer_id), sale.total, "SALE", sale.id)
        printed = _print_receipt(sale)
        return Response(
            {
                "invoice_number": invoice.fiscal_number,
                "sale_id": str(sale.id),
                "subtotal": str(sale.subtotal),
                "discount": str(sale.discount),
                "tax_total": str(sale.tax_total),
                "timbre": str(sale.timbre_amount_snapshot),
                "total": str(sale.total),
                "total_display": display(sale.total),
                "printed": printed,
                "receipt_url": f"/api/v1/pos/sales/{sale.id}/receipt",
            },
            status=status.HTTP_201_CREATED,
        )


class ReturnView(APIView):
    """POST /api/v1/pos/return — issue a credit note (AVOIR). Gated by void_refund (spec §10/§13)."""

    permission_classes = [capability("void_refund")]

    @transaction.atomic
    def post(self, request):
        ser = ReturnSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        original = (
            Sale.objects.filter(pk=d.get("original_sale")).first()
            if d.get("original_sale") else None
        )
        ret = process_return(
            original_sale=original,
            items=[
                {"product_id": i["product"], "qty": i["qty"],
                 "unit_price": i["unit_price"], "tax_rate": i["tax_rate"]}
                for i in d["items"]
            ],
            reason=d["reason"],
            refund_method=d["refund_method"],
            terminal=(original.terminal if original else "C1"),
            created_by=request.user,
        )
        return Response(
            {"number": ret.number, "total": str(ret.total), "refund_method": ret.refund_method},
            status=status.HTTP_201_CREATED,
        )


class SalesReportView(APIView):
    """GET /api/v1/reports/sales?from=YYYY-MM-DD&to=YYYY-MM-DD (spec §15).

    Optional filters: store, payment, product_type (book|furniture), terminal.
    Response is additive: existing KPI keys unchanged; series / category_mix / etc. appended.
    """

    permission_classes = [CanViewReports]

    def get(self, request):
        date_from = parse_date(request.query_params.get("from", ""))
        date_to = parse_date(request.query_params.get("to", ""))
        if not date_from or not date_to:
            return Response({"detail": "from and to (YYYY-MM-DD) are required."},
                            status=status.HTTP_400_BAD_REQUEST)
        store = request.query_params.get("store") or None
        payment = request.query_params.get("payment") or None
        product_type = request.query_params.get("product_type") or None
        terminal = request.query_params.get("terminal") or None
        if product_type and product_type not in ("book", "furniture"):
            return Response({"detail": "product_type must be book or furniture."},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(sales_report(
            date_from, date_to,
            store=store, payment_method=payment,
            product_type=product_type, terminal=terminal,
        ))


class ConsolidatedReportView(APIView):
    """GET /api/v1/reports/consolidated?from=&to= — cross-store roll-up by store_code (Phase 6).
    On a cloud hub this disaggregates chain-wide sales per store; on a single store it returns one
    line (the empty store_code)."""

    permission_classes = [CanViewReports]

    def get(self, request):
        date_from = parse_date(request.query_params.get("from", ""))
        date_to = parse_date(request.query_params.get("to", ""))
        if not date_from or not date_to:
            return Response({"detail": "from and to (YYYY-MM-DD) are required."},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(consolidated_sales_by_store(date_from, date_to))


class TrialBalanceView(APIView):
    """GET /api/v1/reports/trial-balance — post sales/receipts/payments to the GL, then return
    the trial balance (debits must equal credits). Spec §15 (Phase 5)."""

    permission_classes = [CanViewReports]

    def get(self, request):
        from teyssir.ledger.services import post_all_to_gl, trial_balance
        post_all_to_gl()
        return Response(trial_balance())


class FinancialsView(APIView):
    """GET /api/v1/reports/financials — income statement + balance sheet from the GL. Spec §15."""

    permission_classes = [CanViewReports]

    def get(self, request):
        from teyssir.ledger.services import financial_statements, post_all_to_gl
        post_all_to_gl()
        return Response(financial_statements())


class VatDeclarationView(APIView):
    """GET /api/v1/reports/vat?from=YYYY-MM-DD&to=YYYY-MM-DD — TVA collected − deductible. Spec §15."""

    permission_classes = [CanViewReports]

    def get(self, request):
        from teyssir.ledger.services import post_all_to_gl, vat_declaration
        date_from = parse_date(request.query_params.get("from", ""))
        date_to = parse_date(request.query_params.get("to", ""))
        if not date_from or not date_to:
            return Response({"detail": "from and to (YYYY-MM-DD) are required."},
                            status=status.HTTP_400_BAD_REQUEST)
        post_all_to_gl()
        return Response(vat_declaration(date_from, date_to))


class SupplierViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = Supplier.objects.filter(active=True).order_by("name")
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticated]


class PurchaseOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          viewsets.GenericViewSet):
    """Purchase orders: list/retrieve/create + receive against the PO (spec §"Purchase mgmt")."""

    queryset = PurchaseOrder.objects.all().order_by("-created_at")
    serializer_class = PurchaseOrderSerializer
    permission_classes = [capability("manage_purchasing")]

    def create(self, request):
        ser = POCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        supplier = Supplier.objects.filter(pk=d["supplier"]).first()
        if not supplier:
            return Response({"detail": "supplier not found"}, status=status.HTTP_404_NOT_FOUND)
        po = create_po(
            supplier=supplier, created_by=request.user,
            items=[{"product_id": i["product"], "qty": i["qty"], "unit_cost": i["unit_cost"]}
                   for i in d["items"]],
        )
        return Response(PurchaseOrderSerializer(po).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        po = self.get_object()
        receive_po(po=po)
        po.refresh_from_db()
        return Response(PurchaseOrderSerializer(po).data)


class PurchaseInvoiceView(APIView):
    """POST /api/v1/purchasing/invoices — record a supplier invoice (books TVA déductible)."""

    permission_classes = [capability("manage_purchasing")]

    def post(self, request):
        ser = PurchaseInvoiceCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        supplier = Supplier.objects.filter(pk=d["supplier"]).first()
        if not supplier:
            return Response({"detail": "supplier not found"}, status=status.HTTP_404_NOT_FOUND)
        po = PurchaseOrder.objects.filter(pk=d.get("po")).first() if d.get("po") else None
        inv = record_purchase_invoice(
            supplier=supplier, supplier_number=d["supplier_number"],
            subtotal=d["subtotal"], tva_total=d["tva_total"], po=po,
        )
        return Response({"id": str(inv.id), "total": str(inv.total), "status": inv.status},
                        status=status.HTTP_201_CREATED)


class ReceiveView(APIView):
    """POST /api/v1/purchasing/receive — ad-hoc goods receipt; rolls weighted-avg cost (§14.2)."""

    permission_classes = [capability("manage_purchasing")]

    def post(self, request):
        ser = ReceiveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        supplier = Supplier.objects.filter(pk=d["supplier"]).first()
        if not supplier:
            return Response({"detail": "supplier not found"}, status=status.HTTP_404_NOT_FOUND)
        result = receive_direct(
            supplier=supplier, terminal=d["terminal"],
            items=[{"product_id": i["product"], "qty": i["qty"], "unit_cost": i["unit_cost"]}
                   for i in d["items"]],
        )
        return Response(result, status=status.HTTP_201_CREATED)


class StockTakeView(APIView):
    permission_classes = [capability("adjust_stock")]

    def post(self, request):
        ser = StockTakeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        result = post_stocktake(
            [{"product_id": i["product"], "counted_qty": i["counted_qty"]} for i in d["items"]],
            terminal=d["terminal"],
        )
        return Response(result, status=status.HTTP_201_CREATED)


class QuotationCreateView(APIView):
    permission_classes = [capability("create_sale")]

    def post(self, request):
        ser = QuotationCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        q = create_quotation(
            customer_id=d["customer"], terminal=d["terminal"], valid_until=d.get("valid_until"),
            items=[{"product_id": i["product"], "qty": i["qty"],
                    "unit_price": i["unit_price"], "tax_rate": i["tax_rate"]} for i in d["items"]],
            created_by=request.user,
        )
        return Response(
            {"id": str(q.id), "subtotal": str(q.subtotal),
             "tax_total": str(q.tax_total), "total": str(q.total)},
            status=status.HTTP_201_CREATED,
        )


class QuotationConvertView(APIView):
    permission_classes = [capability("create_sale")]

    def post(self, request, pk):
        q = Quotation.objects.filter(pk=pk).first()
        if not q:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            invoice = convert_quotation(
                q, payment_method=request.data.get("payment_method", "CASH"),
                created_by=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        sale = Sale.objects.filter(invoice=invoice).first() or invoice.sale
        _print_receipt(sale)
        return Response(
            {"invoice_number": invoice.fiscal_number, "total_display": display(sale.total)},
            status=status.HTTP_201_CREATED,
        )


class ReservationCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = ReservationCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        r = create_reservation(
            product_id=d["product"], qty=d["qty"], customer_id=d["customer"],
            terminal=d["terminal"], expires_at=d.get("expires_at"), created_by=request.user,
        )
        return Response({"id": str(r.id), "status": r.status}, status=status.HTTP_201_CREATED)


class ReservationReleaseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        r = Reservation.objects.filter(pk=pk).first()
        if not r:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        release_reservation(r)
        return Response({"id": str(r.id), "status": r.status})


class CashOpenView(APIView):
    permission_classes = [capability("open_close_cash")]

    def post(self, request):
        session = open_session(
            user=request.user, terminal=request.data.get("terminal", "C1"),
            opening_float=request.data.get("opening_float", 0),
        )
        return Response(
            {"session": str(session.id), "terminal": session.terminal,
             "opening_float": str(session.opening_float)},
            status=status.HTTP_201_CREATED,
        )


class CashXView(APIView):
    permission_classes = [capability("open_close_cash")]

    def get(self, request):
        session = current_session(request.query_params.get("terminal", "C1"))
        if not session:
            return Response({"detail": "no open session"}, status=status.HTTP_404_NOT_FOUND)
        return Response(x_report(session))


class CashZView(APIView):
    permission_classes = [capability("open_close_cash")]

    def post(self, request):
        session = current_session(request.data.get("terminal", "C1"))
        if not session:
            return Response({"detail": "no open session"}, status=status.HTTP_404_NOT_FOUND)
        counted = request.data.get("counted_cash")
        if counted in (None, ""):
            return Response({"detail": "counted_cash required"}, status=status.HTTP_400_BAD_REQUEST)
        return Response(z_report(session, counted))


def _image_payload(request, img):
    return {"id": str(img.id), "url": request.build_absolute_uri(img.image.url),
            "kind": img.kind, "is_primary": img.is_primary, "order": img.order}


def _scan_job_payload(request, job, images=None):
    """Shape a ScanJob for the client. When DONE, the reviewable draft fields are merged at the
    top level (backward-compatible with the original synchronous scan response)."""
    from teyssir.catalog.models import ProductImage, ScanJob

    if images is None:
        images = ProductImage.objects.filter(id__in=job.image_ids)
    body = {
        "job_id": str(job.id),
        "status": job.status.lower(),                       # pending | done | failed
        "stage": job.stage or ("done" if job.status == ScanJob.DONE else
                               "failed" if job.status == ScanJob.FAILED else "queued"),
        "progress": 0 if job.progress is None else int(job.progress),
        "image_ids": [str(i) for i in job.image_ids],
        "images": [_image_payload(request, img) for img in images],
    }
    if job.status == ScanJob.DONE and job.result:
        body.update(job.result)
    if job.status == ScanJob.FAILED:
        body["error"] = job.error
    return body


class BookScanView(APIView):
    """POST multipart {images[], isbn?} -> store draft images, enqueue OCR + ISBN enrichment as a
    ScanJob, return the job. With the inline executor the job is already DONE (the draft is in the
    response); with the thread executor it returns 202 pending and the client polls ScanJobView.
    Docs/BOOK-OCR-ARCHITECTURE.md §6."""

    permission_classes = [capability("edit_product")]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        from teyssir.catalog.bookscan.jobs import enqueue_scan
        from teyssir.catalog.models import ProductImage, ScanJob

        files = request.FILES.getlist("images")
        isbn = (request.data.get("isbn") or "").strip()
        images = [
            ProductImage.objects.create(
                image=f,
                kind=(ProductImage.COVER if i == 0 else
                      ProductImage.BACK if i == 1 else ProductImage.OTHER),
                order=i)
            for i, f in enumerate(files)
        ]
        job = ScanJob.objects.create(isbn=isbn, image_ids=[str(img.id) for img in images])
        enqueue_scan(job.id)
        job.refresh_from_db()
        code = status.HTTP_202_ACCEPTED if job.status == ScanJob.PENDING else status.HTTP_200_OK
        return Response(_scan_job_payload(request, job, images), status=code)


class ScanJobView(APIView):
    """GET /catalog/books/scan/<job_id> — poll a scan job until status is done/failed."""

    permission_classes = [capability("edit_product")]

    def get(self, request, pk):
        from teyssir.catalog.models import ScanJob

        job = ScanJob.objects.filter(pk=pk).first()
        if not job:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(_scan_job_payload(request, job))


class BookCreateView(APIView):
    """POST reviewed JSON (+ image_ids) -> create Product + Book + Contributors."""

    permission_classes = [capability("edit_product")]

    def post(self, request):
        from teyssir.catalog.bookscan.services import create_book_from_draft

        d = request.data
        product = create_book_from_draft(
            data=d, image_ids=d.get("image_ids", []),
            sale_price=d.get("sale_price", "0"), origin_terminal=settings.TERMINAL,
        )
        return Response({"id": str(product.id), "sku": product.sku, "name": product.name_fr},
                        status=status.HTTP_201_CREATED)


class ProductImagesView(APIView):
    """GET list / POST add images for a product (docs/BOOK-OCR)."""

    permission_classes = [capability("edit_product")]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, pk):
        from teyssir.catalog.models import ProductImage
        imgs = ProductImage.objects.filter(product_id=pk)
        return Response([_image_payload(request, i) for i in imgs])

    def post(self, request, pk):
        from teyssir.catalog.models import ProductImage
        img = ProductImage.objects.create(
            product_id=pk, image=request.FILES.get("image"),
            kind=request.data.get("kind", ProductImage.OTHER))
        return Response(_image_payload(request, img), status=status.HTTP_201_CREATED)


class ProductImageView(APIView):
    """DELETE an image; PATCH to set it primary."""

    permission_classes = [capability("edit_product")]

    def delete(self, request, pk):
        from teyssir.catalog.models import ProductImage
        ProductImage.objects.filter(pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def patch(self, request, pk):
        from teyssir.catalog.models import ProductImage
        img = ProductImage.objects.filter(pk=pk).first()
        if not img:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.data.get("is_primary"):
            ProductImage.objects.filter(product=img.product).update(is_primary=False)
            ProductImage.objects.filter(pk=pk).update(is_primary=True)
        return Response(_image_payload(request, ProductImage.objects.get(pk=pk)))


def _print_receipt(sale, *, duplicate=False):
    """Best-effort: print the receipt on the local node's printer; never block a sale.

    Returns True if bytes were sent to a backend, False on any failure.
    Reprints (``duplicate=True``) mark DUPLICATA and skip the drawer kick — same sale, no new fiscal doc.
    """
    import logging
    import os

    log = logging.getLogger("teyssir.printing")
    target = os.environ.get("TEYSSIR_PRINTER", "dummy")
    try:
        from teyssir.printing.devices import send
        from teyssir.printing.receipt import render_sale_receipt

        payload = render_sale_receipt(sale, duplicate=duplicate, kick=not duplicate)
        n = send(payload)
        ok = n > 0
        log.info(
            "receipt print sale=%s target=%s bytes=%s duplicate=%s ok=%s",
            getattr(sale, "id", None), target, n, duplicate, ok,
        )
        return ok
    except Exception as exc:
        log.warning(
            "receipt print failed sale=%s target=%s duplicate=%s err=%s",
            getattr(sale, "id", None), target, duplicate, exc,
        )
        return False


class SaleReceiptView(APIView):
    """GET /pos/sales/<id>/receipt — plain-text receipt (preview / reprint / save as .txt).

    Thermal ESC/POS is sent automatically at checkout; this endpoint lets the cashier
    preview or download the same content without hardware. ``?print=1`` reprints
    without creating a new sale (DUPLICATA).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from django.http import HttpResponse

        from teyssir.printing.receipt import render_text
        from teyssir.sales.models import Sale

        sale = Sale.objects.filter(pk=pk, status=Sale.FINALIZED).first()
        if not sale:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        fmt = (request.query_params.get("format") or "text").lower()
        do_print = request.query_params.get("print") == "1"
        text = render_text(sale, duplicate=do_print)
        if fmt == "json":
            printed = False
            if do_print:
                printed = _print_receipt(sale, duplicate=True)
            return Response({
                "sale_id": str(sale.id),
                "text": text,
                "invoice": getattr(getattr(sale, "invoice", None), "fiscal_number", ""),
                "printed": printed if do_print else None,
            })
        # Re-print to the thermal device on demand (same sale — no duplicate fiscal doc)
        printed = False
        if do_print:
            printed = _print_receipt(sale, duplicate=True)
        name = f"receipt-{getattr(getattr(sale, 'invoice', None), 'fiscal_number', pk)}.txt"
        resp = HttpResponse(text, content_type="text/plain; charset=utf-8")
        resp["Content-Disposition"] = f'inline; filename="{name}"'
        if do_print:
            resp["X-Teyssir-Printed"] = "1" if printed else "0"
        return resp


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    caps = sorted(
        p.split(".", 1)[-1]
        for p in request.user.get_all_permissions()
        if p.startswith("accounts.")
    )
    return Response({
        "username": request.user.get_username(),
        "language": getattr(request.user, "preferred_language", "fr"),
        "terminal": request.headers.get("X-Terminal", ""),
        "store_code": settings.STORE_CODE,
        "role": settings.ROLE,
        "is_superuser": bool(request.user.is_superuser),
        "capabilities": caps,
    })


@api_view(["GET"])
@permission_classes([capability("configure_system")])
def diagnostics(request):
    """GET /api/v1/diagnostics — admin/owner node health for the Diagnostics UI."""
    from teyssir.core.diagnostics import collect_diagnostics

    ping = request.query_params.get("ping", "1") not in ("0", "false", "no")
    return Response(collect_diagnostics(ping_llm=ping))
