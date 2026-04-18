# finance/urls.py
from django.urls import path
from .views import (
    FinanceReportView,
    TemporalMemberListCreateView,
)

urlpatterns = [
    path("report/", FinanceReportView.as_view(), name="finance-report"),

    # Temporal daily payments
    path("temporal/", TemporalMemberListCreateView.as_view(), name="temporal-list-create"),
]
