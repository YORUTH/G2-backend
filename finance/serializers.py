from rest_framework import serializers



class FinanceRowSerializer(serializers.Serializer):
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    status = serializers.CharField()        # new / renewal
    member_state = serializers.CharField()  # pending / active
    package_type = serializers.CharField()
    months = serializers.IntegerField()
    base_amount = serializers.IntegerField()
    card_fee = serializers.IntegerField()
    total_paid = serializers.IntegerField()
    paid_at = serializers.DateTimeField()


class FinanceSummarySerializer(serializers.Serializer):
    active_total = serializers.IntegerField()
    pending_total = serializers.IntegerField()
    card_fees_total = serializers.IntegerField()
    grand_total = serializers.IntegerField()
