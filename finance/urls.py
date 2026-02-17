# finance/urls.py
from django.urls import path
from .views import FinanceReportView

urlpatterns = [
    path("report/", FinanceReportView.as_view(), name="finance-report"),
]
