"""
Full test suite for the gym membership backend.

Run with:
    python manage.py test members.tests -v 2

Cloudinary and barcode uploads are mocked throughout so no network
calls are made during testing.

Phone numbers use E164 format (+251...) throughout because
django-phonenumber-field normalises local Ethiopian numbers on save.
"""

from unittest.mock import patch
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password
from rest_framework.test import APITestCase
from rest_framework import status

from accounts.models import User
from .models import (
    MemberProfile,
    CheckIn,
    Locker,
    LockerAssignment,
    PendingMember,
    assign_locker,
)
from .constants import PACKAGE_DURATION


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

CLOUDINARY_MOCK = {"secure_url": "https://res.cloudinary.com/test/image/upload/test.png"}


def make_user(phone="+251911000001", role="member", password="pass1234", **kwargs):
    return User.objects.create_user(
        phone_number=phone,
        role=role,
        password=password,
        first_name=kwargs.pop("first_name", "Test"),
        last_name=kwargs.pop("last_name", "User"),
        **kwargs,
    )


def make_receptionist(phone="+251911999001"):
    return make_user(phone=phone, role="receptionist")


def make_trainer(phone="+251911999002"):
    return make_user(phone=phone, role="trainer")


def make_profile(user=None, package_type="bronze", **kwargs):
    """Create a MemberProfile with all I/O mocked."""
    if user is None:
        user = make_user()
    now = timezone.now()
    months = PACKAGE_DURATION[package_type]
    end_date = kwargs.pop("end_date", now + timedelta(days=30 * months))
    start_date = kwargs.pop("start_date", now)
    with patch("members.models.upload", return_value=CLOUDINARY_MOCK), \
         patch("barcode.writer.ImageWriter.save"):
        return MemberProfile.objects.create(
            user=user,
            gender=kwargs.pop("gender", "male"),
            package_type=package_type,
            start_date=start_date,
            end_date=end_date,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# 1. Model tests
# ---------------------------------------------------------------------------

class TestMemberProfileModel(TestCase):

    def test_barcode_generated_on_first_save(self):
        user = make_user(phone="+251911000010")
        profile = make_profile(user=user)
        self.assertTrue(profile.barcode)
        self.assertEqual(len(profile.barcode), 10)

    def test_barcode_not_regenerated_on_update(self):
        """BUG 5 regression — Cloudinary must only be called once."""
        user = make_user(phone="+251911000011")
        with patch("members.models.upload", return_value=CLOUDINARY_MOCK) as mock_upload, \
             patch("barcode.writer.ImageWriter.save"):
            profile = MemberProfile.objects.create(
                user=user, gender="male", package_type="bronze",
                end_date=timezone.now() + timedelta(days=30),
            )
            calls_after_create = mock_upload.call_count
            profile.weight = 75.0
            profile.save(update_fields=["weight"])
            self.assertEqual(mock_upload.call_count, calls_after_create)

    def test_expired_member_deactivated_on_save(self):
        user = make_user(phone="+251911000012")
        make_profile(
            user=user,
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=1),
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_end_date_duration_no_off_by_one(self):
        """BUG 7 regression — end_date must be exactly 30*months with no +1."""
        user = make_user(phone="+251911000013")
        profile = make_profile(user=user, package_type="gold")
        expected_days = 30 * PACKAGE_DURATION["gold"]
        actual_days = (profile.end_date.date() - profile.start_date.date()).days
        self.assertEqual(actual_days, expected_days)


class TestLockerModel(TestCase):

    def test_cooldown_clears_when_expired(self):
        locker = Locker.objects.create(
            locker_number=1,
            status="cooldown",
            cooldown_until=timezone.now() - timedelta(seconds=1),
        )
        locker.refresh_status()
        locker.refresh_from_db()
        self.assertEqual(locker.status, "available")
        self.assertIsNone(locker.cooldown_until)

    def test_cooldown_stays_while_active(self):
        locker = Locker.objects.create(
            locker_number=2,
            status="cooldown",
            cooldown_until=timezone.now() + timedelta(days=2),
        )
        locker.refresh_status()
        locker.refresh_from_db()
        self.assertEqual(locker.status, "cooldown")

    def test_assignment_expires_and_starts_cooldown(self):
        user = make_user(phone="+251911000020")
        profile = make_profile(user=user)
        locker = Locker.objects.create(locker_number=10, status="active")
        assignment = LockerAssignment.objects.create(
            member=profile,
            locker=locker,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        assignment.refresh_status()
        locker.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "expired")
        self.assertEqual(locker.status, "cooldown")

    def test_assign_locker_function_happy_path(self):
        user = make_user(phone="+251911000021")
        profile = make_profile(user=user)
        Locker.objects.create(locker_number=20, status="available")
        assignment = assign_locker(member=profile, locker_number=20)
        self.assertEqual(assignment.member, profile)
        self.assertEqual(Locker.objects.get(locker_number=20).status, "active")

    def test_assign_locker_rejects_double_assignment(self):
        from django.core.exceptions import ValidationError
        user = make_user(phone="+251911000022")
        profile = make_profile(user=user)
        Locker.objects.create(locker_number=21, status="available")
        Locker.objects.create(locker_number=22, status="available")
        assign_locker(member=profile, locker_number=21)
        with self.assertRaises(ValidationError):
            assign_locker(member=profile, locker_number=22)

    def test_assign_locker_rejects_cooldown_locker(self):
        from django.core.exceptions import ValidationError
        user = make_user(phone="+251911000023")
        profile = make_profile(user=user)
        Locker.objects.create(
            locker_number=23,
            status="cooldown",
            cooldown_until=timezone.now() + timedelta(days=2),
        )
        with self.assertRaises(ValidationError):
            assign_locker(member=profile, locker_number=23)


# ---------------------------------------------------------------------------
# 2. Permissions
# ---------------------------------------------------------------------------

class TestPermissions(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.trainer = make_trainer()
        self.member_user = make_user(phone="+251911001001")

    def test_member_create_requires_receptionist(self):
        self.client.force_authenticate(user=self.member_user)
        resp = self.client.post(reverse("member-create"), {})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_locker_list_requires_receptionist(self):
        self.client.force_authenticate(user=self.trainer)
        resp = self.client.get(reverse("locker-list"))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_pending_list_requires_receptionist(self):
        self.client.force_authenticate(user=self.member_user)
        resp = self.client.get(reverse("pending-list"))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_instant_renewal_requires_receptionist(self):
        self.client.force_authenticate(user=self.member_user)
        resp = self.client.post(reverse("instant-renewal", kwargs={"pk": 1}), {})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_blocked_from_member_list(self):
        resp = self.client.get(reverse("members-list"))
        self.assertIn(resp.status_code, [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ])


# ---------------------------------------------------------------------------
# 3. Member create
# ---------------------------------------------------------------------------

class TestMemberCreate(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.url = reverse("member-create")
        self.valid_payload = {
            "first_name": "Abebe",
            "last_name": "Kebede",
            "phone_number": "+251911100001",
            "password": "securepass",
            "gender": "male",
            "package_type": "bronze",
        }

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_create_member_success(self, *_):
        resp = self.client.post(self.url, self.valid_payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            User.objects.filter(phone_number="+251911100001").exists()
        )

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_duplicate_phone_rejected(self, *_):
        self.client.post(self.url, self.valid_payload)
        resp = self.client.post(self.url, self.valid_payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_required_fields_rejected(self):
        resp = self.client.post(self.url, {"first_name": "Only"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_package_type_rejected(self):
        resp = self.client.post(self.url, {**self.valid_payload, "package_type": "platinum"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 4. Member list and search
# ---------------------------------------------------------------------------

class TestMemberList(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        make_profile(user=make_user(phone="+251911200001", first_name="Dawit"))
        make_profile(user=make_user(phone="+251911200002", first_name="Hana"), package_type="gold")

    def test_list_returns_members(self):
        resp = self.client.get(reverse("members-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["count"], 2)

    def test_search_by_first_name(self):
        resp = self.client.get(reverse("members-list") + "?search=Dawit")
        names = [r["first_name"] for r in resp.data["results"]]
        self.assertIn("Dawit", names)

    def test_left_days_in_every_result(self):
        resp = self.client.get(reverse("members-list"))
        for m in resp.data["results"]:
            self.assertIn("left_days", m)

    def test_expired_member_deactivated_on_list(self):
        u = make_user(phone="+251911200099")
        make_profile(
            user=u,
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=1),
        )
        self.client.get(reverse("members-list"))
        u.refresh_from_db()
        self.assertFalse(u.is_active)


# ---------------------------------------------------------------------------
# 5. Barcode check-in
# ---------------------------------------------------------------------------

class TestBarcodeCheckIn(APITestCase):
    # BarcodeCheckInView uses AllowAny — no authentication needed

    def setUp(self):
        self.user = make_user(phone="+251911300001")
        self.profile = make_profile(user=self.user)

    def test_checkin_success(self):
        resp = self.client.post(
            reverse("barcode-checkin", kwargs={"barcode_number": self.profile.barcode})
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["valid"])
        self.assertEqual(CheckIn.objects.filter(member=self.profile).count(), 1)

    def test_invalid_barcode_returns_404(self):
        resp = self.client.post(
            reverse("barcode-checkin", kwargs={"barcode_number": "0000000000"})
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.data["valid"])

    def test_expired_member_rejected(self):
        u = make_user(phone="+251911300002")
        p = make_profile(
            user=u,
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=1),
        )
        resp = self.client.post(
            reverse("barcode-checkin", kwargs={"barcode_number": p.barcode})
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.data["valid"])

    def test_multiple_checkins_allowed(self):
        url = reverse("barcode-checkin", kwargs={"barcode_number": self.profile.barcode})
        for _ in range(3):
            self.client.post(url)
        self.assertEqual(CheckIn.objects.filter(member=self.profile).count(), 3)


# ---------------------------------------------------------------------------
# 6. Recent check-ins
# ---------------------------------------------------------------------------

class TestRecentCheckIns(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.profile = make_profile(user=make_user(phone="+251911400001"))
        CheckIn.objects.create(member=self.profile, timestamp=timezone.now())
        CheckIn.objects.create(
            member=self.profile,
            timestamp=timezone.now() - timedelta(days=2),
        )

    def test_default_range_is_24_hours(self):
        resp = self.client.get(reverse("recent-checkins"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["results"]["checkins"]), 1)

    def test_month_range_returns_all(self):
        resp = self.client.get(reverse("recent-checkins") + "?range=month")
        self.assertEqual(len(resp.data["results"]["checkins"]), 2)

    def test_response_includes_member_counts(self):
        resp = self.client.get(reverse("recent-checkins"))
        self.assertIn("active_members", resp.data["results"])
        self.assertIn("inactive_members", resp.data["results"])


# ---------------------------------------------------------------------------
# 7. Attendance calendar
# ---------------------------------------------------------------------------

class TestAttendanceCalendar(APITestCase):

    def setUp(self):
        self.user = make_user(phone="+251911500001")
        self.profile = make_profile(user=self.user)
        self.client.force_authenticate(user=self.user)
        CheckIn.objects.create(member=self.profile, timestamp=timezone.now())

    def test_calendar_returns_current_month(self):
        resp = self.client.get(reverse("member-calendar"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("days", resp.data)
        self.assertGreater(resp.data["total_attended_days"], 0)

    def test_calendar_specific_month(self):
        resp = self.client.get(reverse("member-calendar") + "?month=1&year=2024")
        self.assertEqual(len(resp.data["days"]), 31)

    def test_non_member_blocked(self):
        self.client.force_authenticate(user=make_receptionist(phone="+251911500099"))
        resp = self.client.get(reverse("member-calendar"))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# 8. Upcoming expirations
# ---------------------------------------------------------------------------

class TestUpcomingExpirations(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)

    def test_members_expiring_within_5_days_shown(self):
        u = make_user(phone="+251911600001")
        make_profile(user=u, end_date=timezone.now() + timedelta(days=3))
        resp = self.client.get(reverse("upcoming-expirations"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreater(resp.data["count"], 0)

    def test_phone_number_not_null(self):
        """WARN 2 + serialization regression — phone must be a string."""
        u = make_user(phone="+251911600002")
        make_profile(user=u, end_date=timezone.now() + timedelta(days=2))
        resp = self.client.get(reverse("upcoming-expirations"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        phones = [m["phone"] for m in resp.data["members_expiring_soon"]]
        self.assertIn("+251911600002", phones)

    def test_members_beyond_5_days_excluded(self):
        u = make_user(phone="+251911600003")
        make_profile(user=u, end_date=timezone.now() + timedelta(days=10))
        resp = self.client.get(reverse("upcoming-expirations"))
        phones = [m["phone"] for m in resp.data["members_expiring_soon"]]
        self.assertNotIn("+251911600003", phones)


# ---------------------------------------------------------------------------
# 9. Locker endpoints
# ---------------------------------------------------------------------------

class TestLockerAssignment(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.profile = make_profile(
            user=make_user(phone="+251911700001"), package_type="gold"
        )

    def test_assign_locker_success(self):
        resp = self.client.post(reverse("locker-assign"), {
            "member_id": self.profile.id,
            "locker_number": 50,
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["locker_number"], 50)

    def test_double_assign_rejected(self):
        self.client.post(reverse("locker-assign"), {
            "member_id": self.profile.id, "locker_number": 51,
        })
        resp = self.client.post(reverse("locker-assign"), {
            "member_id": self.profile.id, "locker_number": 52,
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_member_rejected(self):
        resp = self.client.post(reverse("locker-assign"), {
            "member_id": 99999, "locker_number": 53,
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_locker_list_days_left_not_null(self):
        """BUG 3 regression — days_left was always null before fix."""
        self.client.post(reverse("locker-assign"), {
            "member_id": self.profile.id, "locker_number": 54,
        })
        resp = self.client.get(reverse("locker-list"))
        active = next(l for l in resp.data if l["locker_number"] == 54)
        self.assertIsNotNone(active["days_left"])
        self.assertGreater(active["days_left"], 0)

    def test_unassigned_locker_days_left_is_none(self):
        Locker.objects.create(locker_number=55, status="available")
        resp = self.client.get(reverse("locker-list"))
        locker = next(l for l in resp.data if l["locker_number"] == 55)
        self.assertIsNone(locker["days_left"])

    def test_cooldown_locker_rejected(self):
        Locker.objects.create(
            locker_number=56, status="cooldown",
            cooldown_until=timezone.now() + timedelta(days=2),
        )
        resp = self.client.post(reverse("locker-assign"), {
            "member_id": self.profile.id, "locker_number": 56,
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 10. Pending — new
# ---------------------------------------------------------------------------

class TestNewPendingMember(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.url = reverse("pending-new-create")
        self.valid_payload = {
            "first_name": "Tigist", "last_name": "Alemu",
            "phone_number": "+251911800001", "gender": "female",
            "package_type": "silver", "password": "mypassword",
        }

    def test_create_pending_new_success(self):
        resp = self.client.post(self.url, self.valid_payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            PendingMember.objects.filter(
                phone_number="+251911800001", pending_type="new"
            ).exists()
        )

    def test_activation_mode_always_delayed(self):
        self.client.post(self.url, {**self.valid_payload, "activation_mode": "instant"})
        pending = PendingMember.objects.get(phone_number="+251911800001")
        self.assertEqual(pending.activation_mode, "delayed")

    def test_password_is_hashed(self):
        self.client.post(self.url, self.valid_payload)
        pending = PendingMember.objects.get(phone_number="+251911800001")
        self.assertTrue(check_password("mypassword", pending.password))

    def test_duplicate_phone_rejected(self):
        self.client.post(self.url, self.valid_payload)
        resp = self.client.post(self.url, self.valid_payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 11. Pending — renewal
# ---------------------------------------------------------------------------

class TestRenewalPending(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.url = reverse("pending-renewal-create")
        self.user = make_user(phone="+251911900001")
        self.profile = make_profile(
            user=self.user,
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=1),
        )
        self.user.is_active = False
        self.user.save()

    def test_renewal_pending_created(self):
        resp = self.client.post(self.url, {
            "member_id": self.profile.id, "package_type": "silver",
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            PendingMember.objects.filter(
                phone_number="+251911900001", pending_type="renewal"
            ).exists()
        )

    def test_active_member_cannot_be_renewed(self):
        self.user.is_active = True
        self.user.save()
        resp = self.client.post(self.url, {
            "member_id": self.profile.id, "package_type": "silver",
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_renewal_rejected(self):
        """Expired member cannot be added to pending list twice."""
        self.client.post(self.url, {"member_id": self.profile.id, "package_type": "silver"})
        resp = self.client.post(self.url, {"member_id": self.profile.id, "package_type": "gold"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            PendingMember.objects.filter(
                phone_number="+251911900001",
                pending_type="renewal",
                status="pending",
            ).count(),
            1,
        )

    def test_invalid_member_rejected(self):
        resp = self.client.post(self.url, {"member_id": 99999, "package_type": "silver"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 12. Activate pending member
# ---------------------------------------------------------------------------

class TestActivatePending(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)

    def _pending_new(self, phone="+251911901001", package_type="bronze"):
        return PendingMember.objects.create(
            first_name="Test", last_name="User",
            phone_number=phone, gender="male",
            package_type=package_type,
            password=make_password("pass1234"),
            pending_type="new", activation_mode="delayed",
            recorded_by=self.receptionist,
        )

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_activate_new_creates_user_with_password(self, *_):
        """BUG 2 regression."""
        pending = self._pending_new(phone="+251911901001")
        resp = self.client.post(reverse("pending-activate", kwargs={"pk": pending.pk}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        user = User.objects.get(phone_number="+251911901001")
        self.assertTrue(check_password("pass1234", user.password))
        self.assertTrue(MemberProfile.objects.filter(user=user).exists())

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_activate_preserves_audit_trail(self, *_):
        """WARN 4 regression — record must not be deleted."""
        pending = self._pending_new(phone="+251911901002")
        self.client.post(reverse("pending-activate", kwargs={"pk": pending.pk}))
        pending.refresh_from_db()
        self.assertEqual(pending.status, "activated")
        self.assertIsNotNone(pending.activated_at)

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_activate_renewal_reactivates_member(self, *_):
        """BUG 1 regression — lookup by phone, not pending.member."""
        u = make_user(phone="+251911901003")
        profile = make_profile(
            user=u,
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=1),
        )
        u.is_active = False
        u.save()

        pending = PendingMember.objects.create(
            first_name=u.first_name, last_name=u.last_name,
            phone_number=str(u.phone_number),
            gender="male", package_type="silver",
            password=u.password, pending_type="renewal",
            activation_mode="delayed", recorded_by=self.receptionist,
        )
        resp = self.client.post(reverse("pending-activate", kwargs={"pk": pending.pk}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        u.refresh_from_db()
        profile.refresh_from_db()
        self.assertTrue(u.is_active)
        self.assertGreater(profile.end_date, timezone.now())

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_activate_sets_correct_end_date(self, *_):
        """BUG 6 regression — start_date must be datetime, not date."""
        pending = self._pending_new(phone="+251911901004", package_type="gold")
        self.client.post(reverse("pending-activate", kwargs={"pk": pending.pk}))
        profile = MemberProfile.objects.get(user__phone_number="+251911901004")
        expected_days = 30 * PACKAGE_DURATION["gold"]
        actual_days = (profile.end_date.date() - profile.start_date.date()).days
        self.assertEqual(actual_days, expected_days)

    def test_nonexistent_pending_returns_404(self):
        resp = self.client.post(reverse("pending-activate", kwargs={"pk": 99999}))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# 13. Instant renewal
# ---------------------------------------------------------------------------

class TestInstantRenewal(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)
        self.user = make_user(phone="+251911902001")
        self.profile = make_profile(
            user=self.user,
            start_date=timezone.now() - timedelta(days=60),
            end_date=timezone.now() - timedelta(days=1),
        )
        self.user.is_active = False
        self.user.save()

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_activates_member(self, *_):
        resp = self.client.post(
            reverse("instant-renewal", kwargs={"pk": self.profile.pk}),
            {"package_type": "gold"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_end_date_uses_months_not_days(self, *_):
        """WARN 5 regression — gold must give ~180 days, not 6."""
        self.client.post(
            reverse("instant-renewal", kwargs={"pk": self.profile.pk}),
            {"package_type": "gold"},
        )
        self.profile.refresh_from_db()
        delta = (self.profile.end_date.date() - timezone.now().date()).days
        self.assertGreater(delta, 100)

    def test_requires_receptionist(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            reverse("instant-renewal", kwargs={"pk": self.profile.pk}),
            {"package_type": "bronze"},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_member_returns_404(self):
        resp = self.client.post(
            reverse("instant-renewal", kwargs={"pk": 99999}),
            {"package_type": "bronze"},
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# 14. Pending member list
# ---------------------------------------------------------------------------

class TestPendingMemberList(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)

    def test_only_pending_status_returned(self):
        PendingMember.objects.create(
            first_name="A", last_name="B", phone_number="+251912000001",
            gender="male", package_type="bronze", password=make_password("p"),
            pending_type="new", status="pending", recorded_by=self.receptionist,
        )
        PendingMember.objects.create(
            first_name="C", last_name="D", phone_number="+251912000002",
            gender="female", package_type="silver", password=make_password("p"),
            pending_type="new", status="activated", recorded_by=self.receptionist,
        )
        # PendingMemberListView has no pagination — resp.data is a plain list
        resp = self.client.get(reverse("pending-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(all(r["status"] == "pending" for r in resp.data))

    def test_recent_first_ordering(self):
        phones = ["+251912000010", "+251912000011", "+251912000012"]
        for phone in phones:
            PendingMember.objects.create(
                first_name="X", last_name="Y", phone_number=phone,
                gender="male", package_type="bronze", password=make_password("p"),
                pending_type="new", status="pending", recorded_by=self.receptionist,
            )
        resp = self.client.get(reverse("pending-list"))
        dates = [r["recorded_at"] for r in resp.data if r["phone_number"] in phones]
        self.assertEqual(dates, sorted(dates, reverse=True))


# ---------------------------------------------------------------------------
# 15. Trainer update view
# ---------------------------------------------------------------------------

class TestTrainerUpdateMember(APITestCase):

    def setUp(self):
        self.trainer = make_trainer()
        self.client.force_authenticate(user=self.trainer)
        self.profile = make_profile(user=make_user(phone="+251911950001"))

    @patch("members.models.upload", return_value=CLOUDINARY_MOCK)
    @patch("barcode.writer.ImageWriter.save")
    def test_trainer_can_update_weight_and_height(self, *_):
        resp = self.client.patch(
            reverse("member-trainer-update", kwargs={"pk": self.profile.pk}),
            {"weight": 80.5, "height": 175.0},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.weight, 80.5)

    def test_receptionist_blocked(self):
        self.client.force_authenticate(user=make_receptionist(phone="+251911950099"))
        resp = self.client.patch(
            reverse("member-trainer-update", kwargs={"pk": self.profile.pk}),
            {"weight": 70.0},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_trainer_cannot_change_package_type(self):
        self.client.patch(
            reverse("member-trainer-update", kwargs={"pk": self.profile.pk}),
            {"package_type": "diamond"},
        )
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.package_type, "diamond")


# ---------------------------------------------------------------------------
# 16. My profile view
# ---------------------------------------------------------------------------

class TestMyProfileView(APITestCase):

    def setUp(self):
        self.user = make_user(phone="+251911960001")
        self.profile = make_profile(user=self.user, package_type="silver")
        self.client.force_authenticate(user=self.user)

    def test_member_can_view_own_profile(self):
        resp = self.client.get(reverse("my-profile"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["phone_number"], str(self.user.phone_number))

    def test_profile_is_read_only(self):
        resp = self.client.patch(reverse("my-profile"), {"package_type": "diamond"})
        self.assertIn(resp.status_code, [
            status.HTTP_405_METHOD_NOT_ALLOWED,
            status.HTTP_403_FORBIDDEN,
        ])

    def test_left_days_present_and_positive(self):
        resp = self.client.get(reverse("my-profile"))
        self.assertIn("left_days", resp.data)
        self.assertGreater(resp.data["left_days"], 0)


# ---------------------------------------------------------------------------
# 17. Recent registrations
# ---------------------------------------------------------------------------

class TestRecentRegistrations(APITestCase):

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_authenticate(user=self.receptionist)

    def test_returns_members_registered_today(self):
        u = make_user(phone="+251911970001", first_name="NewJoin")
        make_profile(user=u)
        resp = self.client.get(reverse("recent-registrations"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [r["firstname"] for r in resp.data]
        self.assertIn("NewJoin", names)

    def test_old_registrations_excluded(self):
        u = make_user(phone="+251911970002", first_name="OldJoin")
        make_profile(
            user=u,
            start_date=timezone.now() - timedelta(days=5),
            end_date=timezone.now() + timedelta(days=25),
        )
        resp = self.client.get(reverse("recent-registrations"))
        names = [r["firstname"] for r in resp.data]
        self.assertNotIn("OldJoin", names)

    def test_week_range_param(self):
        resp = self.client.get(reverse("recent-registrations") + "?range=week")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)