from rest_framework import serializers

from teyssir.catalog.models import Product, TaxRate
from teyssir.customers.models import Customer
from teyssir.purchasing.models import Supplier


class CustomerSerializer(serializers.ModelSerializer):
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "matricule_fiscal", "credit_limit", "active", "balance"]

    def get_balance(self, obj):
        from teyssir.customers.services import balance
        return str(balance(obj))


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "matricule_fiscal", "phone", "active"]


class ReceiveLineSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    qty = serializers.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = serializers.DecimalField(max_digits=14, decimal_places=3)


class ReceiveSerializer(serializers.Serializer):
    supplier = serializers.UUIDField()
    terminal = serializers.CharField(required=False, default="C1")
    items = ReceiveLineSerializer(many=True)


class TaxRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxRate
        fields = ["id", "name", "rate_percent", "is_default"]


class ProductSerializer(serializers.ModelSerializer):
    tax_rate_percent = serializers.DecimalField(
        source="tax_rate.rate_percent", max_digits=5, decimal_places=2,
        read_only=True, default=0,
    )

    class Meta:
        model = Product
        fields = [
            "id", "sku", "name_fr", "name_ar", "sale_price", "qty_on_hand",
            "tax_rate", "tax_rate_percent", "is_book",
        ]


class CheckoutLineSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    qty = serializers.DecimalField(max_digits=14, decimal_places=3, default=1)
    unit_price = serializers.DecimalField(max_digits=14, decimal_places=3, required=False)
    discount = serializers.DecimalField(max_digits=14, decimal_places=3, required=False, default=0)


class CheckoutSerializer(serializers.Serializer):
    terminal = serializers.CharField(default="C1")
    lines = CheckoutLineSerializer(many=True)
    payment_method = serializers.ChoiceField(
        choices=["CASH", "CARD", "ACCOUNT"], default="CASH",
    )
    customer = serializers.UUIDField(required=False, allow_null=True)


class ReturnLineSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    qty = serializers.DecimalField(max_digits=14, decimal_places=3, default=1)
    unit_price = serializers.DecimalField(max_digits=14, decimal_places=3)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, default=0)


class ReturnSerializer(serializers.Serializer):
    original_sale = serializers.UUIDField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    refund_method = serializers.ChoiceField(choices=["CASH", "CARD", "ACCOUNT"], default="CASH")
    items = ReturnLineSerializer(many=True)


class QuotationLineInputSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    qty = serializers.DecimalField(max_digits=14, decimal_places=3, default=1)
    unit_price = serializers.DecimalField(max_digits=14, decimal_places=3)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, default=0)


class QuotationCreateSerializer(serializers.Serializer):
    customer = serializers.CharField(required=False, allow_blank=True, default="")
    terminal = serializers.CharField(required=False, default="C1")
    valid_until = serializers.DateField(required=False, allow_null=True)
    items = QuotationLineInputSerializer(many=True)


class ReservationCreateSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    qty = serializers.DecimalField(max_digits=14, decimal_places=3, default=1)
    customer = serializers.CharField(required=False, allow_blank=True, default="")
    terminal = serializers.CharField(required=False, default="C1")
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class StockTakeLineSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    counted_qty = serializers.DecimalField(max_digits=14, decimal_places=3)


class StockTakeSerializer(serializers.Serializer):
    terminal = serializers.CharField(required=False, default="C1")
    items = StockTakeLineSerializer(many=True)
