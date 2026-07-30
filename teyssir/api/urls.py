from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from .views import (
    BarcodeLookupView, CashOpenView, CashXView, CashZView, CatalogSearchView, CategoryListView,
    CheckoutView, CustomerViewSet, PdfToDocxDownloadView, PdfToDocxJobView, PdfToDocxView,
    ProductCreateView, ProductDetailView,
    ProductViewSet,
    QuotationConvertView, QuotationCreateView, ReceiveView, ReservationCreateView,
    BookCreateView, BookScanView, FinancialsView, ProductImageView, ProductImagesView, ScanJobView,
    PurchaseInvoiceView, PurchaseOrderViewSet, ReservationReleaseView, ReturnView,
    SaleReceiptView,
    ConsolidatedReportView, SalesReportView, StockTakeView, SupplierViewSet, TaxRateViewSet,
    TrialBalanceView,
    VatDeclarationView, me,
)

router = DefaultRouter()
router.register("catalog/products", ProductViewSet, basename="product")
router.register("catalog/tax-rates", TaxRateViewSet, basename="taxrate")
router.register("customers", CustomerViewSet, basename="customer")
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("purchasing/orders", PurchaseOrderViewSet, basename="po")

urlpatterns = [
    path("auth/token", obtain_auth_token, name="auth-token"),
    path("me", me, name="me"),
    path("pos/checkout", CheckoutView.as_view(), name="pos-checkout"),
    path("pos/return", ReturnView.as_view(), name="pos-return"),
    path("pos/sales/<uuid:pk>/receipt", SaleReceiptView.as_view(), name="sale-receipt"),
    path("cash/open", CashOpenView.as_view(), name="cash-open"),
    path("cash/x", CashXView.as_view(), name="cash-x"),
    path("cash/z", CashZView.as_view(), name="cash-z"),
    path("quotations", QuotationCreateView.as_view(), name="quotation-create"),
    path("quotations/<uuid:pk>/convert", QuotationConvertView.as_view(), name="quotation-convert"),
    path("reservations", ReservationCreateView.as_view(), name="reservation-create"),
    path("reservations/<uuid:pk>/release", ReservationReleaseView.as_view(), name="reservation-release"),
    path("inventory/stocktake", StockTakeView.as_view(), name="inventory-stocktake"),
    path("purchasing/receive", ReceiveView.as_view(), name="purchasing-receive"),
    path("purchasing/invoices", PurchaseInvoiceView.as_view(), name="purchase-invoice"),
    path("catalog/search", CatalogSearchView.as_view(), name="catalog-search"),
    path("catalog/categories", CategoryListView.as_view(), name="catalog-categories"),
    path("catalog/lookup", BarcodeLookupView.as_view(), name="barcode-lookup"),
    path("catalog/register", ProductCreateView.as_view(), name="product-register"),
    path("tools/pdf-to-docx", PdfToDocxView.as_view(), name="pdf-to-docx"),
    path("tools/pdf-to-docx/<uuid:pk>", PdfToDocxJobView.as_view(), name="pdf-to-docx-job"),
    path("tools/pdf-to-docx/<uuid:pk>/download", PdfToDocxDownloadView.as_view(),
         name="pdf-to-docx-download"),
    path("catalog/products/<uuid:pk>/detail", ProductDetailView.as_view(), name="product-detail"),
    path("catalog/books/scan", BookScanView.as_view(), name="book-scan"),
    path("catalog/books/scan/<uuid:pk>", ScanJobView.as_view(), name="scan-job"),
    path("catalog/books", BookCreateView.as_view(), name="book-create"),
    path("catalog/products/<uuid:pk>/images", ProductImagesView.as_view(), name="product-images"),
    path("catalog/images/<uuid:pk>", ProductImageView.as_view(), name="product-image"),
    path("reports/sales", SalesReportView.as_view(), name="reports-sales"),
    path("reports/consolidated", ConsolidatedReportView.as_view(), name="reports-consolidated"),
    path("reports/trial-balance", TrialBalanceView.as_view(), name="reports-trial-balance"),
    path("reports/financials", FinancialsView.as_view(), name="reports-financials"),
    path("reports/vat", VatDeclarationView.as_view(), name="reports-vat"),
    path("", include(router.urls)),
]
