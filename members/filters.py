import django_filters
from datetime import timedelta
from django.utils import timezone

from .models import MemberProfile


class MemberProfileFilter(django_filters.FilterSet):
    active = django_filters.BooleanFilter(field_name="user__is_active")
    first_name = django_filters.CharFilter(field_name="user__first_name", lookup_expr="icontains")
    last_name = django_filters.CharFilter(field_name="user__last_name", lookup_expr="icontains")
    left_days = django_filters.NumberFilter(method="filter_left_days")

    class Meta:
        model = MemberProfile
        fields = [
            "active",
            "package_type",
            "start_date",
            "end_date",
            "barcode",
            "user__phone_number",
            "user__first_name",
            "user__last_name",
            "left_days",
        ]

    def filter_left_days(self, queryset, name, value):
        # Match members whose end_date is <= `value` days from today
        try:
            days = int(value)
        except (TypeError, ValueError):
            return queryset.none()

        today = timezone.now().date()
        target_date = today + timedelta(days=days)
        return queryset.filter(end_date__date__lte=target_date)


