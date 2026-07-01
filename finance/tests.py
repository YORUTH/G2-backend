from datetime import datetime, timedelta

import pytz
from django.contrib.auth.hashers import make_password
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch

from members.models import Locker, PendingMember, assign_locker
from members.tests import CLOUDINARY_MOCK, make_profile, make_receptionist, make_user

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")


class TestFinanceLockerReport(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.url = reverse("finance-report")

    def _today_params(self):
        today = timezone.now().astimezone(ADDIS_TZ).strftime("%Y-%m-%d")
        return {"from": today, "to": today}

    def _date_params(self, day):
        d = day.astimezone(ADDIS_TZ).strftime("%Y-%m-%d")
        return {"from": d, "to": d}

    def test_expired_member_locker_only_appears_in_today_report(self):
        user = make_user(phone="+251911700001")
        profile = make_profile(
            user=user,
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=1),
        )
        user.is_active = False
        user.save()

        Locker.objects.create(locker_number=70, status="available")
        assign_locker(member=profile, locker_number=70)

        today = timezone.now().astimezone(ADDIS_TZ).strftime("%Y-%m-%d")
        resp = self.client.get(self.url, {"from": today, "to": today})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        locker_rows = [
            row for row in resp.data["rows"]
            if row["status"] == "locker"
            and row["first_name"] == user.first_name
        ]
        self.assertEqual(len(locker_rows), 1)
        self.assertEqual(locker_rows[0]["locker_fee"], 200)
        self.assertEqual(locker_rows[0]["member_state"], "active")
        self.assertEqual(resp.data["summary"]["locker_fees_total"], 200)

    def test_active_member_same_day_locker_fee_on_membership_row(self):
        user = make_user(phone="+251911700002")
        profile = make_profile(
            user=user,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
        )
        Locker.objects.create(locker_number=71, status="available")
        assign_locker(member=profile, locker_number=71)

        resp = self.client.get(self.url, self._today_params())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        membership_rows = [
            r for r in resp.data["rows"]
            if r["first_name"] == user.first_name and r["status"] == "new"
        ]
        self.assertEqual(len(membership_rows), 1)
        self.assertEqual(membership_rows[0]["locker_fee"], 200)
        locker_only = [
            r for r in resp.data["rows"]
            if r["first_name"] == user.first_name and r["status"] == "locker"
        ]
        self.assertEqual(len(locker_only), 0)


class TestFinancePendingPaymentDate(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.url = reverse("finance-report")

    def _date_params(self, day):
        d = day.astimezone(ADDIS_TZ).strftime("%Y-%m-%d")
        return {"from": d, "to": d}

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_payment_counted_on_pending_day_not_activation_day(self, *_):
        monday = ADDIS_TZ.localize(datetime(2026, 6, 1, 10, 0))
        friday = ADDIS_TZ.localize(datetime(2026, 6, 5, 14, 0))

        pending = PendingMember.objects.create(
            first_name="Sara",
            last_name="Kebede",
            phone_number="+251911700100",
            gender="female",
            package_type="bronze",
            password=make_password("pass1234"),
            pending_type="new",
            recorded_by=self.receptionist,
        )
        PendingMember.objects.filter(pk=pending.pk).update(recorded_at=monday)

        with patch("members.views.timezone.now", return_value=friday):
            resp = self.client.post(reverse("pending-activate", kwargs={"pk": pending.pk}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        monday_report = self.client.get(self.url, self._date_params(monday))
        self.assertEqual(monday_report.status_code, status.HTTP_200_OK)
        monday_rows = [
            r for r in monday_report.data["rows"]
            if r["first_name"] == "Sara" and r["member_state"] == "active"
        ]
        self.assertEqual(len(monday_rows), 1)
        self.assertGreater(monday_rows[0]["total_paid"], 0)

        friday_report = self.client.get(self.url, self._date_params(friday))
        self.assertEqual(friday_report.status_code, status.HTTP_200_OK)
        friday_rows = [
            r for r in friday_report.data["rows"]
            if r["first_name"] == "Sara" and r["member_state"] in ("active", "pending")
        ]
        self.assertEqual(len(friday_rows), 0)

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_month_report_counts_member_once(self, *_):
        monday = ADDIS_TZ.localize(datetime(2026, 7, 1, 9, 0))
        wednesday = ADDIS_TZ.localize(datetime(2026, 7, 3, 11, 0))

        pending = PendingMember.objects.create(
            first_name="Dawit",
            last_name="Alemu",
            phone_number="+251911700101",
            gender="male",
            package_type="silver",
            password=make_password("pass1234"),
            pending_type="new",
            recorded_by=self.receptionist,
        )
        PendingMember.objects.filter(pk=pending.pk).update(recorded_at=monday)

        with patch("members.views.timezone.now", return_value=wednesday):
            self.client.post(reverse("pending-activate", kwargs={"pk": pending.pk}))

        month_report = self.client.get(self.url, {"month": "2026-07"})
        self.assertEqual(month_report.status_code, status.HTTP_200_OK)
        payment_rows = [
            r for r in month_report.data["rows"]
            if r["first_name"] == "Dawit" and r["member_state"] in ("active", "pending")
        ]
        self.assertEqual(len(payment_rows), 1)

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_instant_renewal_uses_start_date_when_no_payment_recorded_at(self, *_):
        user = make_user(phone="+251911700102")
        profile = make_profile(
            user=user,
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=1),
        )
        user.is_active = False
        user.save()
        profile.payment_recorded_at = None
        profile.save(update_fields=["payment_recorded_at"])

        today = timezone.now()
        with patch("members.views.timezone.now", return_value=today):
            resp = self.client.post(
                reverse("instant-renewal", kwargs={"pk": profile.pk}),
                {"package_type": "bronze"},
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        profile.refresh_from_db()
        self.assertIsNone(profile.payment_recorded_at)

        today_str = today.astimezone(ADDIS_TZ).strftime("%Y-%m-%d")
        report = self.client.get(reverse("finance-report"), {"from": today_str, "to": today_str})
        rows = [r for r in report.data["rows"] if r["first_name"] == user.first_name]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "renewal")
