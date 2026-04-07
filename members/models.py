from django.db import models, transaction
from accounts.models import User
from django.utils import timezone
from datetime import timedelta
import pytz
import barcode
from django.core.exceptions import ValidationError
from barcode.writer import ImageWriter
from io import BytesIO
from cloudinary.uploader import upload
from django.db.models import F
import random
from .constants import get_package_price

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")

GENDER_CHOICES = [
    ("male", "Male"),
    ("female", "Female"),
]

PACKAGE_CHOICES = [
    ("bronze", "Bronze - 1 Month"),
    ("silver", "Silver - 3 Months"),
    ("gold", "Gold - 6 Months"),
    ("diamond", "Diamond - 12 Months"),
    ("bronze_women", "Bronze (Women, 4-6 PM) - 1 Month"),
    ("silver_women", "Silver (Women) - 3 Months"),
    ("gold_women", "Gold (Women) - 6 Months"),
    ("diamond_women", "Diamond (Women) - 12 Months"),
]

PACKAGE_DURATION = {
    "bronze": 1,
    "silver": 3,
    "gold": 6,
    "diamond": 12,
    "bronze_women": 1,
    "silver_women": 3,
    "gold_women": 6,
    "diamond_women": 12,
}


class MemberProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    weight = models.FloatField(null=True, blank=True)
    height = models.FloatField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    age = models.IntegerField(null=True, blank=True)
    image = models.ImageField(null=True, blank=True)
    image_url = models.URLField(max_length=500, null=True, blank=True)
    barcode = models.CharField(max_length=50, unique=True)
    barcode_image = models.URLField(max_length=500, null=True, blank=True)
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES)
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()
    is_renewal = models.BooleanField(default=False)

    @property
    def package_price(self):
        return get_package_price(self.package_type)

    def clean(self):
        if self.package_type.endswith("_women") and self.gender != "female":
            raise ValidationError("Women package types are only allowed for female members.")

    def generate_unique_barcode(self):
        while True:
            code = str(random.randint(1000000000, 9999999999))
            if not MemberProfile.objects.filter(barcode=code).exists():
                return code

    def save(self, *args, **kwargs):
        now = timezone.now()

        # FIX (BUG 7): was timedelta(days=(30 * months) + 1) which added an
        # extra day compared to every other place end_date is calculated.
        # Unified to 30 * months with no offset.
        if self.package_type in PACKAGE_DURATION and self.start_date and not self.end_date:
            months = PACKAGE_DURATION[self.package_type]

            start_local = self.start_date
            if timezone.is_naive(start_local):
                start_local = ADDIS_TZ.localize(start_local)
            else:
                start_local = start_local.astimezone(ADDIS_TZ)

            end_local = start_local + timedelta(days=30 * months)
            self.end_date = end_local.astimezone(pytz.UTC)

        # FIX (BUG 5): track whether we just generated a new barcode so we
        # only hit Cloudinary on first creation, not on every subsequent save.
        barcode_is_new = False
        if not self.barcode:
            self.barcode = self.generate_unique_barcode()
            barcode_is_new = True

        if barcode_is_new:
            EAN = barcode.get_barcode_class("code128")
            ean = EAN(self.barcode, writer=ImageWriter())
            buffer = BytesIO()
            ean.write(buffer)
            buffer.seek(0)
            upload_result = upload(
                buffer,
                folder="barcodes",
                public_id=f"{self.user.first_name}_{self.barcode}",
                overwrite=True,
            )
            self.barcode_image = upload_result["secure_url"]

        if self.end_date and self.end_date < now:
            self.user.is_active = False
            self.user.save(update_fields=["is_active"])

        super().save(*args, **kwargs)

        if self.image and not str(self.image).startswith("http"):
            file_obj = getattr(self.image, "file", None)
            if file_obj:
                image_upload = upload(
                    file_obj,
                    folder="member_images",
                    public_id=f"{self.user.first_name}_{self.user.id}_profile",
                    overwrite=True,
                )
                self.image_url = image_upload.get("secure_url")
                super().save(update_fields=["image_url"])


class CheckIn(models.Model):
    member = models.ForeignKey(
        MemberProfile, on_delete=models.CASCADE, related_name="checkins"
    )
    timestamp = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.member.user.first_name} checked in at {self.timestamp}"


LOCKER_STATUS = [
    ("available", "Available"),
    ("active", "Active"),
    ("cooldown", "Cooldown"),
]


class Locker(models.Model):
    locker_number = models.PositiveIntegerField(unique=True)
    status = models.CharField(max_length=20, choices=LOCKER_STATUS, default="available")
    cooldown_until = models.DateTimeField(null=True, blank=True)

    def refresh_status(self):
        now = timezone.now()
        if self.status == "cooldown" and self.cooldown_until and now >= self.cooldown_until:
            self.status = "available"
            self.cooldown_until = None
            self.save(update_fields=["status", "cooldown_until"])

    def __str__(self):
        return f"Locker {self.locker_number} ({self.status})"


ASSIGNMENT_STATUS = [
    ("active", "Active"),
    ("expired", "Expired"),
]


class LockerAssignment(models.Model):
    member = models.ForeignKey(
        MemberProfile,
        on_delete=models.CASCADE,
        related_name="locker_assignments"
    )
    locker = models.ForeignKey(
        Locker,
        on_delete=models.PROTECT,
        related_name="assignments"
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    expired_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=ASSIGNMENT_STATUS, default="active")

    def refresh_status(self):
        now = timezone.now()
        if self.status == "active" and now >= self.expires_at:
            self.status = "expired"
            self.expired_at = now
            self.save(update_fields=["status", "expired_at"])

    def __str__(self):
        return f"{self.member.user.first_name} → Locker {self.locker.locker_number}"


LOCKER_DURATION_DAYS = 30
LOCKER_COOLDOWN_DAYS = 3


@transaction.atomic
def assign_locker(*, member, locker_number):
    now = timezone.now()
    gender = member.gender

    member = MemberProfile.objects.select_for_update().get(pk=member.pk)

    existing = member.locker_assignments.filter(status="active").first()
    if existing:
        if existing.expires_at > now:
            raise ValidationError("Member already has an active locker.")
        existing.status = "expired"
        existing.expired_at = now
        existing.save(update_fields=["status", "expired_at"])

    locker = Locker.objects.select_for_update().get(locker_number=locker_number)

    LockerAssignment.objects.filter(
        locker=locker, status="active", expires_at__lte=now, member__gender=gender,
    ).update(status="expired", expired_at=now)

    if LockerAssignment.objects.filter(
        locker=locker, status="active", member__gender=gender,
    ).exists():
        raise ValidationError(
            f"Locker is currently assigned to a {gender} member."
        )

    cooldown_threshold = now - timedelta(days=LOCKER_COOLDOWN_DAYS)
    latest_expired = LockerAssignment.objects.filter(
        locker=locker,
        status="expired",
        member__gender=gender,
        expired_at__gt=cooldown_threshold,
    ).order_by("-expired_at").first()


    if latest_expired:
        cooldown_end = latest_expired.expired_at + timedelta(days=LOCKER_COOLDOWN_DAYS)
        if cooldown_end > now:
            remaining = cooldown_end - now
            raise ValidationError(
                f"Locker is in cooldown for {gender}s. Available in {remaining.days} day(s)."
            )

    expires_at = now + timedelta(days=LOCKER_DURATION_DAYS)
    assignment = LockerAssignment.objects.create(
        member=member,
        locker=locker,
        assigned_at=now,
        expires_at=expires_at,
    )

    return assignment


class PendingMember(models.Model):

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("activated", "Activated"),
    ]

    PENDING_TYPE_CHOICES = [
        ("new", "New"),
        ("renewal", "Renewal"),
    ]

    ACTIVATION_MODE_CHOICES = [
        ("instant", "Instant"),
        ("delayed", "Delayed"),
    ]

    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    phone_number = models.CharField(max_length=20, unique=True)
    weight = models.FloatField(null=True, blank=True)
    height = models.FloatField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    age = models.IntegerField(null=True, blank=True)
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES)
    password = models.CharField(max_length=128)
    pending_type = models.CharField(max_length=10, choices=PENDING_TYPE_CHOICES)
    activation_mode = models.CharField(
        max_length=10,
        choices=ACTIVATION_MODE_CHOICES,
        default="delayed"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="pending_created"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_profile = models.OneToOneField(
        MemberProfile,
        null=True,
        blank=True,
        on_delete=models.PROTECT
    )

    @property
    def package_price(self):
        return get_package_price(self.package_type)

    def clean(self):
        if self.pending_type == "new" and self.activation_mode == "instant":
            raise ValidationError("New members cannot use instant activation.")
        if self.package_type.endswith("_women") and self.gender != "female":
            raise ValidationError("Women package types are only allowed for female members.")

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.pending_type})"