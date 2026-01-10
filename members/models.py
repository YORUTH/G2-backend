from django.db import models, transaction
from accounts.models import User
from django.utils import timezone
from datetime import timedelta
import pytz
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
from cloudinary.uploader import upload

from django.db.models import F
import random
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
]

PACKAGE_DURATION = {
    "bronze": 1,
    "silver": 3,
    "gold": 6,
    "diamond": 12,
}


class MemberProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    weight = models.FloatField(null=True, blank=True)
    height = models.FloatField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    age = models.IntegerField(null=True, blank=True)
    image = models.ImageField( null=True, blank=True)
    image_url = models.URLField(max_length=500, null=True, blank=True)
    barcode = models.CharField(max_length=50, unique=True)
    barcode_image = models.URLField(max_length=500, null=True, blank=True)
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES)
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()


    def generate_unique_barcode(self):

        while True:
            code = str(random.randint(1000000000, 9999999999))
            if not MemberProfile.objects.filter(barcode=code).exists():
                return code

 
    def save(self, *args, **kwargs):
        now = timezone.now()


        # Calculate end_date based on package and start_date
        if self.package_type in PACKAGE_DURATION and self.start_date and not self.end_date:
            months = PACKAGE_DURATION[self.package_type]
            
            # Convert start_date to Addis Ababa timezone
            start_local = self.start_date
            if timezone.is_naive(start_local):
                start_local = ADDIS_TZ.localize(start_local)
            else:
                start_local = start_local.astimezone(ADDIS_TZ)
            
            # Add package duration
            end_local = start_local + timedelta(days=30 * months)

            # Convert back to UTC before saving
            self.end_date = end_local.astimezone(pytz.UTC)

        # Generate barcode only if empty
        if not self.barcode:
            self.barcode = self.generate_unique_barcode()

        # Generate barcode image
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

        # Deactivate user if membership expired
        if self.end_date and self.end_date < now:
            self.user.is_active = False
            self.user.save(update_fields=["is_active"])

        super().save(*args, **kwargs)

        # Upload image URL if provided
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
