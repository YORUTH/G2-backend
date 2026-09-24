from rest_framework import serializers
from .models import TemporalMember


class TemporalMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemporalMember
        fields = ["id", "full_name", "age", "amount", "paid_at"]
        read_only_fields = ["id", "amount"]


class FinanceRowSerializer(serializers.Serializer):
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    status = serializers.CharField()
    member_state = serializers.CharField()
    package_type = serializers.CharField()
    months = serializers.IntegerField()
    base_amount = serializers.IntegerField()
    card_fee = serializers.IntegerField()
    locker_fee = serializers.IntegerField()
    boxing_fee = serializers.IntegerField()
    discount = serializers.IntegerField(default=0)
    total_paid = serializers.IntegerField()
    paid_at = serializers.DateTimeField()


class FinanceSummarySerializer(serializers.Serializer):
    active_total = serializers.IntegerField()
    pending_total = serializers.IntegerField()
    temporal_total = serializers.IntegerField()
    card_fees_total = serializers.IntegerField()
    locker_fees_total = serializers.IntegerField()
    boxing_fees_total = serializers.IntegerField()
    discount_total = serializers.IntegerField(default=0)
    grand_total = serializers.IntegerField()


class FinanceReportSerializer(serializers.Serializer):
    rows = FinanceRowSerializer(many=True)
    summary = FinanceSummarySerializer()


class LockerFinanceSummarySerializer(serializers.Serializer):
    locker_total = serializers.IntegerField()
    grand_total = serializers.IntegerField()


class LockerFinanceReportSerializer(serializers.Serializer):
    rows = FinanceRowSerializer(many=True)
    summary = LockerFinanceSummarySerializer()
