from datetime import timedelta

import pytz
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from members.models import Locker, assign_locker
from members.tests import make_manager, make_profile, make_receptionist, make_user

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")


class TestFinanceLockerReport(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.url = reverse("finance-report")

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

    def test_manager_can_access_finance_report(self):
        manager = make_manager()
        self.client.force_authenticate(user=manager)
        today = timezone.now().astimezone(ADDIS_TZ).strftime("%Y-%m-%d")
        resp = self.client.get(self.url, {"from": today, "to": today})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("rows", resp.data)
        self.assertIn("summary", resp.data)
