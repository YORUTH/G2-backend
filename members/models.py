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
from django.db.models import DateTimeField, ExpressionWrapper, F
import random
from .constants import get_package_price
import threading

ADDIS_TZ = pytz.timezone("Africa/Addis_Ababa")

GENDER_CHOICES = [
    ("male", "Male"),
    ("female", "Female"),
]

PACKAGE_CHOICES = [
    ("bronze", "Bronze - 1 Month"),
    ("chrome", "Chrome - 2 Months"),
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
    "chrome": 2,
    "silver": 3,
    "gold": 6,
    "diamond": 12,
    "bronze_women": 1,
    "silver_women": 3,
    "gold_women": 6,
    "diamond_women": 12,
}

def upload_barcode_background(profile_id, barcode_str, first_name):
    """Uploads the barcode to Cloudinary in a background thread."""
    try:
        EAN = barcode.get_barcode_class("code128")
        ean = EAN(barcode_str, writer=ImageWriter())
        buffer = BytesIO()
        ean.write(buffer)
        buffer.seek(0)
        
        upload_result = upload(
            buffer,
            folder="barcodes",
            public_id=f"{first_name}_{barcode_str}",
            overwrite=True,
        )
        url = upload_result.get("secure_url")
        if url:
            MemberProfile.objects.filter(id=profile_id).update(barcode_image=url)
    except Exception as e:
        print(f"Failed to upload barcode in background: {e}")

def upload_profile_image_background(profile_id, first_name, user_id, file_content):
    """Uploads the profile image to Cloudinary in a background thread."""
    try:
        image_upload = upload(
            file_content,
            folder="member_images",
            public_id=f"{first_name}_{user_id}_profile",
            overwrite=True,
        )
        url = image_upload.get("secure_url")
        if url:
            MemberProfile.objects.filter(id=profile_id).update(image_url=url)
    except Exception as e:
        print(f"Failed to upload profile image in background: {e}")


class MemberProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    weight = models.FloatField(default=0.0)
    height = models.FloatField(default=0.0)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    age = models.IntegerField(default=0)
    image = models.ImageField(null=True, blank=True)
    image_url = models.URLField(max_length=500, null=True, blank=True)
    barcode = models.CharField(max_length=50, unique=True)
    barcode_image = models.URLField(max_length=500, null=True, blank=True)
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES)
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()
    is_renewal = models.BooleanField(default=False)
    # Boxing specific fields
    has_boxing = models.BooleanField(default=False)
    boxing_expires_at = models.DateTimeField(null=True, blank=True)
    payment_recorded_at = models.DateTimeField(null=True, blank=True, default=None)
    discount_amount = models.PositiveIntegerField(default=0)
    machine_user_id = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    is_face_enrolled = models.BooleanField(default=False)
    face_enrolled_at = models.DateTimeField(null=True, blank=True)

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

        # Generate barcode if new
        barcode_is_new = False
        if not self.barcode:
            self.barcode = self.generate_unique_barcode()
            barcode_is_new = True

        if self.end_date and self.end_date < now:
            self.user.is_active = False
            self.user.save(update_fields=["is_active"])

        super().save(*args, **kwargs)
        
        # After saving to DB, trigger background uploads
        if barcode_is_new and self.pk:
            threading.Thread(
                target=upload_barcode_background, 
                args=(self.pk, self.barcode, self.user.first_name),
                daemon=True
            ).start()

        if self.image and not str(self.image).startswith("http") and self.pk:
            file_obj = getattr(self.image, "file", None)
            if file_obj:
                # Read the file content into memory so the thread can access it 
                # after the main request closes the file pointer
                file_content = file_obj.read()
                threading.Thread(
                    target=upload_profile_image_background, 
                    args=(self.pk, self.user.first_name, self.user.id, file_content),
                    daemon=True
                ).start()


class CheckIn(models.Model):
    member = models.ForeignKey(
        MemberProfile, on_delete=models.CASCADE, related_name="checkins"
    )
    timestamp = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        if not self.pk:
            from datetime import datetime, time
            current_time = self.timestamp if self.timestamp else timezone.now()
            today = timezone.localtime(current_time).date()
            start_of_day = timezone.make_aware(datetime.combine(today, time.min))
            end_of_day = timezone.make_aware(datetime.combine(today, time.max))
            existing = CheckIn.objects.filter(
                member=self.member,
                timestamp__gte=start_of_day,
                timestamp__lte=end_of_day
            ).order_by('-timestamp').first()
            if existing:
                # Reuse existing check-in; DO NOT save a duplicate timestamp in database
                self.id = existing.id
                self.pk = existing.pk
                self.timestamp = existing.timestamp
                return
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.member.user.first_name} checked in at {self.timestamp}"


LOCKER_STATUS = [
    ("available", "Available"),
    ("active", "Active"),
]


class Locker(models.Model):
    locker_number = models.PositiveIntegerField(unique=True)
    status = models.CharField(max_length=20, choices=LOCKER_STATUS, default="available")

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
    status = models.CharField(max_length=20, choices=ASSIGNMENT_STATUS, default="active")

    def refresh_status(self):
        now = timezone.now()
        if self.status == "active" and now >= self.expires_at:
            self.status = "expired"
            self.save(update_fields=["status"])

    def __str__(self):
        return f"{self.member.user.first_name} → Locker {self.locker.locker_number}"


LOCKER_DURATION_DAYS = 30


def cleanup_locker_assignments(now=None):
    """Mark overdue active assignments as expired."""
    now = now or timezone.now()

    LockerAssignment.objects.filter(
        status="active",
        expires_at__lte=now,
    ).update(status="expired")


@transaction.atomic
def assign_locker(*, member, locker_number):
    now = timezone.now()

    cleanup_locker_assignments(now)

    member = MemberProfile.objects.select_for_update().get(pk=member.pk)

    # If the member already has an active assignment, handle it
    existing = member.locker_assignments.filter(
        status="active", expires_at__gt=now,
    ).select_related("locker").first()
    if existing:
        if existing.locker.locker_number == locker_number:
            raise ValidationError("Member already has this locker.")
        existing.delete()

    locker = Locker.objects.select_for_update().get(locker_number=locker_number)

    gender = member.gender

    # Block if there's an active (non-expired) assignment on this locker for this gender
    if LockerAssignment.objects.filter(
        locker=locker,
        status="active",
        expires_at__gt=now,
        member__gender=gender,
    ).exists():
        raise ValidationError(f"Locker is currently assigned to another {gender} member.")

    # Remove any expired assignments on this locker for this gender to free it up
    LockerAssignment.objects.filter(
        locker=locker,
        status="expired",
        member__gender=gender,
    ).delete()

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
    weight = models.FloatField(default=0.0)
    height = models.FloatField(default=0.0)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    age = models.IntegerField(default=0)
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES)
    password = models.CharField(max_length=128)
    pending_type = models.CharField(max_length=10, choices=PENDING_TYPE_CHOICES)
    wants_boxing = models.BooleanField(default=False)
    discount_amount = models.PositiveIntegerField(default=0)
    machine_user_id = models.CharField(max_length=50, null=True, blank=True)
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


class PendingMemberHistory(models.Model):
    PENDING_TYPE_CHOICES = PendingMember.PENDING_TYPE_CHOICES
    ACTIVATION_MODE_CHOICES = PendingMember.ACTIVATION_MODE_CHOICES

    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    phone_number = models.CharField(max_length=20)
    weight = models.FloatField(default=0.0)
    height = models.FloatField(default=0.0)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    age = models.IntegerField(default=0)
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES)
    pending_type = models.CharField(max_length=10, choices=PENDING_TYPE_CHOICES)
    wants_boxing = models.BooleanField(default=False)
    discount_amount = models.PositiveIntegerField(default=0)
    machine_user_id = models.CharField(max_length=50, null=True, blank=True)
    activation_mode = models.CharField(max_length=10, choices=ACTIVATION_MODE_CHOICES)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="pending_history_recorded",
    )
    activated_profile = models.ForeignKey(
        MemberProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pending_history",
    )
    recorded_at = models.DateTimeField()
    activated_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-activated_at", "-recorded_at"]

    def __str__(self):
        return (
            f"{self.first_name} {self.last_name} "
            f"({self.pending_type}) activated {self.activated_at:%Y-%m-%d %H:%M}"
        )