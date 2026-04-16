from rest_framework import serializers

from .models import (
    CollectionTask,
    CreditorAccount,
    CreditorFollowUp,
    DebtorAccount,
    DebtorFollowUp,
    PaymentDispute,
    PromiseToPay,
)


class DebtorAccountSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = DebtorAccount
        fields = "__all__"


class CreditorAccountSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = CreditorAccount
        fields = "__all__"


class DebtorFollowUpSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = DebtorFollowUp
        fields = "__all__"


class CreditorFollowUpSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = CreditorFollowUp
        fields = "__all__"


class PromiseToPaySerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = PromiseToPay
        fields = "__all__"


class PaymentDisputeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentDispute
        fields = "__all__"


class CollectionTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = CollectionTask
        fields = "__all__"
