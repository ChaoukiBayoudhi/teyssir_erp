from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from .views import (
    CashOpenView, CashXView, CashZView, CheckoutView, CustomerViewSet, ProductViewSet,
    QuotationConvertView, QuotationCreateView, ReceiveView, ReservationCreateView,
    BookCreateView, BookScanView, FinancialsView, ProductImageView, ProductImagesView,
    ReservationReleaseView, ReturnView, SalesReportView, StockTakeView,
    SupplierViewSet, TaxRateViewSet, TrialBalanceView, VatDeclarationView, me,
)

router = DefaultRouter()
router.register("catalog/products", ProductViewSet, basename="product")
router.register("catalog/tax-rates", TaxRateViewSet, basename="taxrate")
router.register("customers", CustomerViewSet, basename="customer")
router.register("suppliers", SupplierViewSet, basename="supplier")

urlpatterns = [
    path("auth/token", obtain_auth_token, name="auth-token"),
    path("me", me, name="me"),
    path("pos/checkout", CheckoutView.as_view(), name="pos-checkout"),
    path("pos/return", ReturnView.as_view(), name="pos-return"),
    path("cash/open", CashOpenView.as_view(), name="cash-open"),
    path("cash/x", CashXView.as_view(), name="cash-x"),
    path("cash/z", CashZView.as_view(), name="cash-z"),
    path("quotations", QuotationCreateView.as_view(), name="quotation-create"),
    path("quotations/<uuid:pk>/convert", QuotationConvertView.as_view(), name="quotation-convert"),
    path("reservations", ReservationCreateView.as_view(), name="reservation-create"),
    path("reservations/<uuid:pk>/release", ReservationReleaseView.as_view(), name="reservation-release"),
    path("inventory/stocktake", StockTakeView.as_view(), name="inventory-stocktake"),
    path("purchasing/receive", ReceiveView.as_view(), name="purchasing-receive"),
    path("catalog/books/scan", BookScanView.as_view(), name="book-scan"),
    path("catalog/books", BookCreateView.as_view(), name="book-create"),
    path("catalog/products/<uuid:pk>/images", ProductImagesView.as_view(), name="product-images"),
    path("catalog/images/<uuid:pk>", ProductImageView.as_view(), name="product-image"),
    path("reports/sales", SalesReportView.as_view(), name="reports-sales"),
    path("reports/trial-balance", TrialBalanceView.as_view(), name="reports-trial-balance"),
    path("reports/financials", FinancialsView.as_view(), name="reports-financials"),
    path("reports/vat", VatDeclarationView.as_view(), name="reports-vat"),
    path("", include(router.urls)),
]
