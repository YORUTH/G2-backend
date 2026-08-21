# finance/urls.py
from django.urls import path
from .views import FinanceReportView, SendTelegramReportView

urlpatterns = [
    path("report/", FinanceReportView.as_view(), name="finance-report"),
    path("send-telegram/", SendTelegramReportView.as_view(), name="send-telegram-report"),
]
